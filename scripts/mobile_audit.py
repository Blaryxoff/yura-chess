"""Capture a mobile PageSpeed Insights snapshot for the five SEO-critical pages.

`docs/plans/product/20260912-seo-improvement.md` (P2) requires a saved mobile
measurement of `/`, `/how-to-play`, `/commands`, `/coach` and `/blindfold` from
before and after each release that can touch them, so a regression is caught by
a number instead of a guess. Run it once before publishing and once after:

    uv run python scripts/mobile_audit.py --label pre-release
    uv run python scripts/mobile_audit.py --label post-release

Each run writes the raw Lighthouse JSON and a short Markdown summary under
`docs/qa/mobile-audits/<date>-<label>/`. Comparing two labels is a diff of
their `summary.md` scores and metrics; nothing here decides on its own whether
a change is a regression.

The public PageSpeed Insights endpoint enforces a small anonymous quota
(effectively unusable for five sequential pages). Set `YURA_CHESS_PAGESPEED_API_KEY`
to a Google API key with the PageSpeed Insights API enabled to lift it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yura_chess.presentation.website import (
    BLINDFOLD_PATH,
    COACH_PATH,
    COMMANDS_PATH,
    HOW_TO_PLAY_PATH,
    LANDING_PATH,
    PUBLIC_SITE_URL,
)

API_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
REQUEST_TIMEOUT_SECONDS = 60.0
# Anonymous callers share a global bucket; spacing requests avoids tripping it mid-run.
REQUEST_SPACING_SECONDS = 2.0

# The exact five pages the P2 card names, in the order it lists them.
AUDITED_PATHS: tuple[str, ...] = (LANDING_PATH, HOW_TO_PLAY_PATH, COMMANDS_PATH, COACH_PATH, BLINDFOLD_PATH)

CATEGORIES = ("performance", "accessibility", "seo", "best-practices")
# Audit ids whose `numericValue` is a Core Web Vital or a close lab proxy for one.
METRIC_AUDITS = ("largest-contentful-paint", "cumulative-layout-shift", "total-blocking-time", "speed-index")


def _slug(path: str) -> str:
    return "home" if path == LANDING_PATH else path.strip("/").replace("/", "-")


def _fetch(url: str, api_key: str | None) -> dict[str, Any]:
    params = {"url": url, "strategy": "mobile", "category": CATEGORIES}
    query = urllib.parse.urlencode(params, doseq=True)
    if api_key:
        query += f"&key={urllib.parse.quote(api_key)}"
    request = urllib.request.Request(f"{API_ENDPOINT}?{query}")  # noqa: S310 - fixed https endpoint
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise SystemExit(f"PageSpeed request for {url} failed: {error.code} {error.read().decode()[:300]}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"PageSpeed request for {url} unreachable: {error}") from error


def _scores(report: dict[str, Any]) -> dict[str, float | None]:
    categories = report["lighthouseResult"]["categories"]
    return {name: (categories[name]["score"] if name in categories else None) for name in CATEGORIES}


def _metrics(report: dict[str, Any]) -> dict[str, str]:
    audits = report["lighthouseResult"]["audits"]
    return {audit_id: audits[audit_id]["displayValue"] for audit_id in METRIC_AUDITS if audit_id in audits}


def _summary_row(path: str, scores: dict[str, float | None], metrics: dict[str, str]) -> str:
    score_cells = " | ".join("—" if value is None else f"{round(value * 100)}" for value in scores.values())
    metric_cells = ", ".join(f"{key}: {value}" for key, value in metrics.items())
    return f"| `{path}` | {score_cells} | {metric_cells} |"


def run(*, label: str, base_url: str, output_root: Path, api_key: str | None) -> int:
    run_dir = output_root / f"{datetime.now(UTC):%Y-%m-%d}-{label}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for index, path in enumerate(AUDITED_PATHS):
        if index:
            time.sleep(REQUEST_SPACING_SECONDS)
        url = urllib.parse.urljoin(base_url, path)
        print(f"auditing {url} ...")
        report = _fetch(url, api_key)
        (run_dir / f"{_slug(path)}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        rows.append(_summary_row(path, _scores(report), _metrics(report)))

    header = "| page | " + " | ".join(CATEGORIES) + " | key metrics |"
    divider = "|" + " --- |" * (len(CATEGORIES) + 2)
    summary = "\n".join([f"# Mobile audit — {label} ({base_url})", "", header, divider, *rows, ""])
    (run_dir / "summary.md").write_text(summary)
    print(summary)
    print(f"saved to {run_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="e.g. pre-release or post-release")
    parser.add_argument("--base-url", default=PUBLIC_SITE_URL, help="site to audit, defaults to production")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/qa/mobile-audits"), help="where to save reports")
    arguments = parser.parse_args()
    api_key = os.environ.get("YURA_CHESS_PAGESPEED_API_KEY")
    return run(label=arguments.label, base_url=arguments.base_url, output_root=arguments.output_dir, api_key=api_key)


if __name__ == "__main__":
    sys.exit(main())
