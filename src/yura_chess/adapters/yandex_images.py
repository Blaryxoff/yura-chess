"""Yandex Dialogs image resources: upload once, reuse by position.

The image is optional by construction. Every failure path — no credentials, no
quota, a slow API, a closed budget — returns `None`, and the caller answers with
speech alone. The PNG is uploaded straight from memory; only the resulting
`image_id` is persisted.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from yura_chess.settings import Settings
from yura_chess.storage.models import BoardImageCacheRow

logger = logging.getLogger(__name__)

UPLOAD_URL_TEMPLATE = "https://dialogs.yandex.net/api/v1/skills/{skill_id}/images"
QUOTA_URL = "https://dialogs.yandex.net/api/v1/status"

Uploader = Callable[[bytes], str | None]
QuotaCheck = Callable[[], tuple[int, int] | None]


class BoardImageCache:
    """`position_hash -> image_id`, with TTL and LRU bounds."""

    def __init__(self, session: Session, ttl_days: int, max_entries: int) -> None:
        self._session = session
        self._ttl_days = ttl_days
        self._max_entries = max_entries

    def get(self, position_hash: str, now: datetime) -> str | None:
        row = self._session.get(BoardImageCacheRow, position_hash)
        if row is None:
            return None
        if row.created_at < now - timedelta(days=self._ttl_days):
            # Expired locally: the remote resource may already be gone.
            self._session.delete(row)
            self._session.flush()
            return None
        row.last_used_at = now
        self._session.flush()
        return row.image_id

    def put(self, position_hash: str, image_id: str, now: datetime) -> None:
        self._session.merge(BoardImageCacheRow(position_hash=position_hash, image_id=image_id, last_used_at=now))
        self._session.flush()

    def evict(self, now: datetime) -> list[str]:
        """Drop expired and least recently used rows; returns the freed image IDs."""
        expired = (
            self._session.query(BoardImageCacheRow)
            .filter(BoardImageCacheRow.created_at < now - timedelta(days=self._ttl_days))
            .all()
        )
        surplus_count = max(0, self._session.query(BoardImageCacheRow).count() - len(expired) - self._max_entries)
        surplus = (
            self._session.query(BoardImageCacheRow)
            .filter(BoardImageCacheRow.created_at >= now - timedelta(days=self._ttl_days))
            .order_by(BoardImageCacheRow.last_used_at.asc())
            .limit(surplus_count)
            .all()
            if surplus_count
            else []
        )
        freed = [row.image_id for row in (*expired, *surplus)]
        for row in (*expired, *surplus):
            self._session.delete(row)
        self._session.flush()
        return freed


class YandexImageClient:
    """Thin HTTP client for the Dialogs image API.

    Deliberately stdlib-only and blocking; callers run it off the event loop and
    under the remaining webhook budget.
    """

    def __init__(self, settings: Settings) -> None:
        self._skill_id = settings.yandex_skill_id
        self._token = settings.yandex_oauth_token
        self._timeout = settings.image_upload_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._skill_id and self._token is not None and self._token.get_secret_value())

    def upload(self, png: bytes) -> str | None:
        if not self.configured:
            return None
        boundary = uuid.uuid4().hex
        body = _multipart_body(png, boundary)
        request = urllib.request.Request(
            UPLOAD_URL_TEMPLATE.format(skill_id=self._skill_id),
            data=body,
            method="POST",
            headers={
                "Authorization": f"OAuth {self._token.get_secret_value()}" if self._token else "",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - fixed https host
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            # Quota, auth and network failures are all "no card this turn".
            logger.warning("board image upload failed", extra={"error": type(error).__name__})
            return None
        image_id = payload.get("image", {}).get("id")
        return image_id if isinstance(image_id, str) and image_id else None

    def quota(self) -> tuple[int, int] | None:
        """`(used_bytes, total_bytes)` for images, or `None` when unavailable."""
        if not self.configured:
            return None
        request = urllib.request.Request(
            QUOTA_URL,
            headers={"Authorization": f"OAuth {self._token.get_secret_value()}" if self._token else ""},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - fixed https host
                images = json.loads(response.read())["images"]["quota"]
            return int(images["used"]), int(images["total"])
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError, OSError) as error:
            logger.warning("image quota check failed", extra={"error": type(error).__name__})
            return None


def _multipart_body(png: bytes, boundary: str) -> bytes:
    return b"".join(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="board.png"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            png,
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )


class BoardImageService:
    """Cache lookup first, upload only if the request can still afford it."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        uploader: Uploader | None = None,
        quota: QuotaCheck | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        client = YandexImageClient(settings)
        self._uploader = uploader or client.upload
        self._quota = quota or client.quota

    async def image_id_for(self, position_hash: str, render: Callable[[], bytes], budget_seconds: float) -> str | None:
        """Return a usable `image_id`, or `None` if the answer must go without one."""
        if not self._settings.board_image_enabled:
            return None
        now = datetime.now()
        cached = await run_in_threadpool(self._lookup, position_hash, now)
        if cached is not None:
            return cached
        if budget_seconds < self._settings.image_upload_timeout_seconds:
            # Not enough of the webhook budget left to risk an upload.
            return None
        image_id = await run_in_threadpool(self._upload, render)
        if image_id is None:
            return None
        await run_in_threadpool(self._store, position_hash, image_id, now)
        return image_id

    def evict_cache(self) -> list[str]:
        """Bounded maintenance pass: never runs blind, only against real quota.

        The API is the source of truth for what is stored remotely, so a failed
        quota check means the cache is left alone rather than trimmed on a guess.
        """
        if self._quota() is None:
            return []
        with self._session_factory.begin() as session:
            return self._cache(session).evict(datetime.now())

    def _lookup(self, position_hash: str, now: datetime) -> str | None:
        with self._session_factory.begin() as session:
            return self._cache(session).get(position_hash, now)

    def _upload(self, render: Callable[[], bytes]) -> str | None:
        return self._uploader(render())

    def _store(self, position_hash: str, image_id: str, now: datetime) -> None:
        with self._session_factory.begin() as session:
            self._cache(session).put(position_hash, image_id, now)

    def _cache(self, session: Session) -> BoardImageCache:
        return BoardImageCache(session, self._settings.board_image_ttl_days, self._settings.board_image_cache_limit)
