"""Capture a mobile CDP snapshot for the five SEO-critical pages.

`docs/plans/product/20260912-seo-improvement.md` (P2) requires a saved mobile
measurement of `/`, `/how-to-play`, `/commands`, `/coach` and `/blindfold` from
before and after each release that can touch them, so a regression is caught by
a number instead of a guess. Point it at a running instance of the site and
run it once before publishing and once after:

    uv run python scripts/mobile_audit.py --label pre-release --base-url http://127.0.0.1:8010
    uv run python scripts/mobile_audit.py --label post-release --base-url http://127.0.0.1:8010

Each run launches a throwaway headless Chrome (`google-chrome`/`chromium` on
PATH), drives it over the Chrome DevTools Protocol with a mobile viewport and
user agent, and writes one JSON record per page plus a `summary.md` under
`docs/qa/mobile-audits/<date>-<label>/`. Comparing two labels is a diff of
their `summary.md`; nothing here decides on its own whether a change is a
regression.

PageSpeed Insights and Lighthouse are not used here: the sandboxed PageSpeed
API quota is `429 RESOURCE_EXHAUSTED` with no key available, and grading a
third-party product is not what this script measures. Talking CDP directly
needs nothing but a Chrome binary, which is already on the machine.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

from yura_chess.presentation.website import (
    BLINDFOLD_PATH,
    COACH_PATH,
    COMMANDS_PATH,
    HOW_TO_PLAY_PATH,
    LANDING_PATH,
)

# The exact five pages the P2 card names, in the order it lists them.
AUDITED_PATHS: tuple[str, ...] = (LANDING_PATH, HOW_TO_PLAY_PATH, COMMANDS_PATH, COACH_PATH, BLINDFOLD_PATH)

MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
)
VIEWPORT_WIDTH = 390
VIEWPORT_HEIGHT = 844

CLS_PROBE_SCRIPT = """
(() => {
  window.__cls = 0;
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) window.__cls += entry.value;
      }
    }).observe({type: "layout-shift", buffered: true});
  } catch (error) {}
})();
"""

METRICS_EXPRESSION = """
(() => {
  const nav = performance.getEntriesByType("navigation")[0];
  const resourceBytes = performance
    .getEntriesByType("resource")
    .reduce((sum, entry) => sum + (entry.transferSize || 0), 0);
  return {
    domContentLoadedMs: nav ? nav.domContentLoadedEventEnd : null,
    loadMs: nav ? nav.loadEventEnd : null,
    transferBytes: (nav ? nav.transferSize : 0) + resourceBytes,
    cls: window.__cls || 0,
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  };
})()
"""


class DevToolsError(RuntimeError):
    pass


class DevToolsSocket:
    """Just enough RFC 6455 plus Chrome DevTools Protocol to drive one page target."""

    def __init__(self, ws_url: str, timeout: float = 20.0) -> None:
        parsed = urllib.parse.urlsplit(ws_url)
        if parsed.hostname is None:
            raise DevToolsError(f"invalid devtools websocket url: {ws_url}")
        self._sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
        self._timeout = timeout
        self._read_buffer = b""
        self._pending_events: deque[dict[str, Any]] = deque()
        self._next_id = count(1)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self._handshake(parsed.netloc, path)

    def _handshake(self, netloc: str, path: str) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {netloc}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode()
        self._sock.sendall(request)
        response = self._recv_until(b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise DevToolsError(f"devtools handshake failed: {response[:200]!r}")

    def _recv_until(self, marker: bytes) -> bytes:
        while marker not in self._read_buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise DevToolsError("devtools socket closed during handshake")
            self._read_buffer += chunk
        index = self._read_buffer.index(marker) + len(marker)
        response, self._read_buffer = self._read_buffer[:index], self._read_buffer[index:]
        return response

    def _recv_exact(self, size: int) -> bytes:
        while len(self._read_buffer) < size:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise DevToolsError("devtools socket closed")
            self._read_buffer += chunk
        data, self._read_buffer = self._read_buffer[:size], self._read_buffer[size:]
        return data

    def _send_frame(self, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x81, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x81, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x81, 0x80 | 127, length)
        self._sock.sendall(header + mask + masked)

    def _recv_frame(self) -> bytes:
        parts: list[bytes] = []
        while True:
            first_two = self._recv_exact(2)
            fin = first_two[0] & 0x80
            opcode = first_two[0] & 0x0F
            length = first_two[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            parts.append(self._recv_exact(length))
            if opcode == 0x8:
                raise DevToolsError("devtools socket closed by peer")
            if fin:
                break
        return b"".join(parts)

    def _read_message(self, deadline: float) -> dict[str, Any] | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        self._sock.settimeout(remaining)
        try:
            return dict(json.loads(self._recv_frame()))
        except TimeoutError:
            return None

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        message_id = next(self._next_id)
        self._send_frame(json.dumps({"id": message_id, "method": method, "params": params or {}}).encode())
        deadline = time.monotonic() + (timeout or self._timeout)
        while True:
            message = self._read_message(deadline)
            if message is None:
                raise DevToolsError(f"{method} timed out waiting for a response")
            if message.get("id") == message_id:
                if "error" in message:
                    raise DevToolsError(f"{method} failed: {message['error']}")
                return dict(message.get("result", {}))
            if "method" in message:
                self._pending_events.append(message)

    def wait_for(self, method: str, timeout: float) -> dict[str, Any]:
        for _ in range(len(self._pending_events)):
            message = self._pending_events.popleft()
            if message.get("method") == method:
                return dict(message.get("params", {}))
            self._pending_events.append(message)
        deadline = time.monotonic() + timeout
        while True:
            message = self._read_message(deadline)
            if message is None:
                raise DevToolsError(f"timed out waiting for event {method}")
            if message.get("method") == method:
                return dict(message.get("params", {}))
            if "method" in message:
                self._pending_events.append(message)

    def close(self) -> None:
        self._sock.close()


def _slug(path: str) -> str:
    return "home" if path == LANDING_PATH else path.strip("/").replace("/", "-")


def _wait_for_devtools_endpoint(port: int, process: subprocess.Popen[bytes], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("chrome exited before its devtools endpoint came up")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5):  # noqa: S310
                return
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError):
            time.sleep(0.2)
    raise SystemExit("chrome devtools endpoint did not come up in time")


def _launch_chrome(port: int, user_data_dir: Path) -> subprocess.Popen[bytes]:
    binary = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if binary is None:
        raise SystemExit("no google-chrome/chromium binary found on PATH")
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, no user input
        [
            binary,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_devtools_endpoint(port, process)
    return process


def _page_target_ws_url(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as response:  # noqa: S310
        targets = json.loads(response.read())
    for target in targets:
        if target.get("type") == "page":
            return str(target["webSocketDebuggerUrl"])
    raise SystemExit("no page target exposed by chrome devtools")


def _prepare_session(ws: DevToolsSocket) -> None:
    ws.call("Page.enable")
    ws.call("Network.enable")
    ws.call("Network.setCacheDisabled", {"cacheDisabled": True})
    ws.call("Network.setUserAgentOverride", {"userAgent": MOBILE_USER_AGENT})
    ws.call(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": VIEWPORT_WIDTH,
            "height": VIEWPORT_HEIGHT,
            "deviceScaleFactor": 3,
            "mobile": True,
        },
    )
    ws.call("Emulation.setTouchEmulationEnabled", {"enabled": True})
    ws.call("Page.addScriptToEvaluateOnNewDocument", {"source": CLS_PROBE_SCRIPT})


def _measure_page(ws: DevToolsSocket, url: str, settle_seconds: float) -> dict[str, Any]:
    ws.call("Page.navigate", {"url": url})
    ws.wait_for("Page.loadEventFired", timeout=20.0)
    time.sleep(settle_seconds)
    result = ws.call("Runtime.evaluate", {"expression": METRICS_EXPRESSION, "returnByValue": True})
    if result.get("exceptionDetails"):
        raise DevToolsError(f"metrics script failed for {url}: {result['exceptionDetails']}")
    return dict(result["result"]["value"])


def _summary_row(path: str, metrics: dict[str, Any]) -> str:
    overflow = "yes" if metrics["documentWidth"] > metrics["viewportWidth"] else "no"
    return (
        f"| `{path}` | {metrics['domContentLoadedMs']:.0f} ms | {metrics['loadMs']:.0f} ms | "
        f"{metrics['transferBytes']:.0f} B | {metrics['cls']:.4f} | "
        f"{overflow} ({metrics['documentWidth']}px vs {metrics['viewportWidth']}px) |"
    )


def run(*, label: str, base_url: str, output_root: Path, port: int, settle_seconds: float) -> int:
    run_dir = output_root / f"{datetime.now(UTC):%Y-%m-%d}-{label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir = Path(tempfile.mkdtemp(prefix="mobile-audit-"))

    process = _launch_chrome(port, user_data_dir)
    try:
        ws = DevToolsSocket(_page_target_ws_url(port))
        try:
            _prepare_session(ws)
            rows = []
            for path in AUDITED_PATHS:
                url = urllib.parse.urljoin(base_url, path)
                print(f"measuring {url} ...")
                metrics = _measure_page(ws, url, settle_seconds)
                record = {"path": path, "url": url, **metrics}
                (run_dir / f"{_slug(path)}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
                rows.append(_summary_row(path, metrics))
        finally:
            ws.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(user_data_dir, ignore_errors=True)

    header = "| page | DOMContentLoaded | load | transfer | CLS | document width vs viewport |"
    divider = "|" + " --- |" * 5
    summary = "\n".join([f"# Mobile audit — {label} ({base_url})", "", header, divider, *rows, ""])
    (run_dir / "summary.md").write_text(summary)
    print(summary)
    print(f"saved to {run_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="e.g. pre-release or post-release")
    parser.add_argument("--base-url", required=True, help="running instance to audit, e.g. http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/qa/mobile-audits"), help="where to save reports")
    parser.add_argument(
        "--port", type=int, default=9333, help="local CDP debugging port for the headless Chrome we launch"
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=0.5,
        help="pause after the load event before reading layout-shift/timing metrics",
    )
    arguments = parser.parse_args()
    return run(
        label=arguments.label,
        base_url=arguments.base_url,
        output_root=arguments.output_dir,
        port=arguments.port,
        settle_seconds=arguments.settle_seconds,
    )


if __name__ == "__main__":
    sys.exit(main())
