"""Tell Yandex and Bing that the public pages changed, without a webmaster console.

IndexNow is a ping: the search engine fetches the key from the site, confirms the
host is ours, then queues the listed URLs. It replaces neither a sitemap nor a
verified property — it only shortens the wait for the next crawl. Google does not
participate; only Search Console can nudge Google.

Run it after every deploy that changes public page content:

    uv run python scripts/submit_indexnow.py

The URL list comes from the sitemap the application itself serves, so a page that
is missing here is missing from the sitemap too.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from yura_chess.presentation.website import (
    INDEXNOW_KEY,
    INDEXNOW_KEY_PATH,
    PUBLIC_SITE_URL,
    SITEMAP_ENTRIES,
)

ENDPOINTS = (
    "https://yandex.com/indexnow",
    "https://api.indexnow.org/indexnow",
)
REQUEST_TIMEOUT_SECONDS = 20.0


def _public_urls() -> list[str]:
    root = PUBLIC_SITE_URL.rstrip("/")
    return [f"{root}{path}" for path, _ in SITEMAP_ENTRIES]


def _verify_key_is_published(host: str) -> None:
    """A submission is rejected outright when the key file is not reachable, so check it first."""
    url = f"https://{host}{INDEXNOW_KEY_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https URL
            body = response.read().decode("utf-8").strip()
    except urllib.error.URLError as error:
        raise SystemExit(f"key file {url} is not reachable: {error}") from error
    if body != INDEXNOW_KEY:
        raise SystemExit(f"key file {url} serves {body!r}, expected {INDEXNOW_KEY!r}")


def _submit(endpoint: str, host: str, urls: list[str]) -> tuple[bool, str]:
    payload = json.dumps(
        {"host": host, "key": INDEXNOW_KEY, "keyLocation": f"https://{host}{INDEXNOW_KEY_PATH}", "urlList": urls}
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - endpoints are fixed https URLs
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            return True, f"{response.status} {response.reason}"
    except urllib.error.HTTPError as error:
        # 422 means the engine disagrees about the URLs; the body says which.
        return False, f"{error.code} {error.reason}: {error.read().decode('utf-8', 'replace')[:200]}"
    except urllib.error.URLError as error:
        return False, f"unreachable: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would be submitted and stop")
    arguments = parser.parse_args()

    host = urlsplit(PUBLIC_SITE_URL).netloc
    urls = _public_urls()
    for url in urls:
        print(f"  {url}")
    if arguments.dry_run:
        return 0

    _verify_key_is_published(host)
    # A silent failure here is worse than no submission at all: the deploy would
    # report success while the changed pages sit unqueued.
    failed = 0
    for endpoint in ENDPOINTS:
        accepted, detail = _submit(endpoint, host, urls)
        print(f"{endpoint} -> {detail}")
        failed += not accepted
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
