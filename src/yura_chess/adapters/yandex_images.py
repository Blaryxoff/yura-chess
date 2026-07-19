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
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import quote

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from yura_chess.settings import Settings
from yura_chess.storage.models import BoardImageCacheRow

logger = logging.getLogger(__name__)

UPLOAD_URL_TEMPLATE = "https://dialogs.yandex.net/api/v1/skills/{skill_id}/images"
DELETE_URL_TEMPLATE = f"{UPLOAD_URL_TEMPLATE}/{{image_id}}"
QUOTA_URL = "https://dialogs.yandex.net/api/v1/status"

Uploader = Callable[[bytes], str | None]
QuotaCheck = Callable[[], tuple[int, int] | None]
Deleter = Callable[[str], bool]


@dataclass(frozen=True)
class EvictionCandidate:
    position_hash: str
    image_id: str
    last_used_at: datetime


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
        row.last_used_at = now
        self._session.flush()
        return row.image_id

    def put(self, position_hash: str, image_id: str, now: datetime) -> None:
        self._session.merge(BoardImageCacheRow(position_hash=position_hash, image_id=image_id, last_used_at=now))
        self._session.flush()

    def count(self) -> int:
        return self._session.query(BoardImageCacheRow).count()

    def eviction_candidates(
        self,
        now: datetime,
        batch_size: int,
        grace_seconds: int,
        emergency: bool,
    ) -> list[EvictionCandidate]:
        """Return idle LRU rows without forgetting their remote IDs."""
        idle_cutoff = now - timedelta(days=self._ttl_days)
        grace_cutoff = now - timedelta(seconds=grace_seconds)
        eligible = self._session.query(BoardImageCacheRow).filter(BoardImageCacheRow.last_used_at < grace_cutoff)
        idle_count = eligible.filter(BoardImageCacheRow.last_used_at < idle_cutoff).count()
        surplus_count = max(0, self.count() - self._max_entries)
        requested = max(idle_count, surplus_count, batch_size if emergency else 0)
        if requested == 0:
            return []
        rows = eligible.order_by(BoardImageCacheRow.last_used_at.asc()).limit(min(batch_size, requested)).all()
        return [
            EvictionCandidate(
                position_hash=row.position_hash,
                image_id=row.image_id,
                last_used_at=row.last_used_at,
            )
            for row in rows
        ]

    def is_current(self, candidate: EvictionCandidate, now: datetime, grace_seconds: int) -> bool:
        row = self._session.get(BoardImageCacheRow, candidate.position_hash)
        return bool(
            row is not None
            and row.image_id == candidate.image_id
            and row.last_used_at == candidate.last_used_at
            and row.last_used_at < now - timedelta(seconds=grace_seconds)
        )

    def forget(self, candidate: EvictionCandidate) -> None:
        row = self._session.get(BoardImageCacheRow, candidate.position_hash)
        if row is not None and row.image_id == candidate.image_id:
            self._session.delete(row)
            self._session.flush()


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

    def delete(self, image_id: str) -> bool:
        """Delete one remote image; a missing image is already clean."""
        if not self.configured:
            return False
        request = urllib.request.Request(
            DELETE_URL_TEMPLATE.format(skill_id=self._skill_id, image_id=quote(image_id, safe="")),
            method="DELETE",
            headers={"Authorization": f"OAuth {self._token.get_secret_value()}" if self._token else ""},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout):  # noqa: S310 - fixed https host
                return True
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return True
            logger.warning("board image delete failed", extra={"error": type(error).__name__, "status": error.code})
            return False
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            logger.warning("board image delete failed", extra={"error": type(error).__name__})
            return False


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
        deleter: Deleter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        client = YandexImageClient(settings)
        self._uploader = uploader or client.upload
        self._quota = quota or client.quota
        self._deleter = deleter or client.delete
        self._quota_blocked = False
        self._upload_lock = Lock()

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
        return await run_in_threadpool(self._upload_if_allowed, position_hash, render, now)

    def maintain_cache(self) -> list[str]:
        """Delete remote LRU images first, then forget their local mappings.

        The API is the source of truth for what is stored remotely, so a failed
        quota check means the cache is left alone rather than trimmed on a guess.
        """
        quota = self._quota()
        if quota is None:
            return []
        self._quota_blocked = self._quota_exhausted(quota)
        now = datetime.now()
        candidates = self._eviction_candidates(now, emergency=self._quota_blocked)
        deleted: list[str] = []
        for candidate in candidates:
            if not self._is_current(candidate, now):
                continue
            if not self._deleter(candidate.image_id):
                continue
            self._forget(candidate)
            deleted.append(candidate.image_id)
        if deleted:
            refreshed = self._quota()
            if refreshed is not None:
                self._quota_blocked = self._quota_exhausted(refreshed)
        return deleted

    def _lookup(self, position_hash: str, now: datetime) -> str | None:
        with self._session_factory.begin() as session:
            return self._cache(session).get(position_hash, now)

    def _upload(self, render: Callable[[], bytes]) -> str | None:
        return self._uploader(render())

    def _store(self, position_hash: str, image_id: str, now: datetime) -> None:
        with self._session_factory.begin() as session:
            self._cache(session).put(position_hash, image_id, now)

    def _upload_if_allowed(self, position_hash: str, render: Callable[[], bytes], now: datetime) -> str | None:
        if not self._upload_lock.acquire(blocking=False):
            return None
        try:
            cached = self._lookup(position_hash, now)
            if cached is not None:
                return cached
            if not self._may_upload():
                return None
            image_id = self._upload(render)
            if image_id is None:
                return None
            self._store(position_hash, image_id, now)
            return image_id
        finally:
            self._upload_lock.release()

    def _may_upload(self) -> bool:
        if self._quota_blocked:
            return False
        with self._session_factory.begin() as session:
            hard_limit = self._settings.board_image_cache_limit + self._settings.board_image_cache_burst
            return self._cache(session).count() < hard_limit

    def _eviction_candidates(self, now: datetime, emergency: bool) -> list[EvictionCandidate]:
        with self._session_factory.begin() as session:
            return self._cache(session).eviction_candidates(
                now,
                self._settings.board_image_cleanup_batch_size,
                self._settings.board_image_cleanup_grace_seconds,
                emergency,
            )

    def _is_current(self, candidate: EvictionCandidate, now: datetime) -> bool:
        with self._session_factory.begin() as session:
            return self._cache(session).is_current(
                candidate,
                now,
                self._settings.board_image_cleanup_grace_seconds,
            )

    def _forget(self, candidate: EvictionCandidate) -> None:
        with self._session_factory.begin() as session:
            self._cache(session).forget(candidate)

    def _quota_exhausted(self, quota: tuple[int, int]) -> bool:
        used, total = quota
        return total <= 0 or used / total >= self._settings.board_image_quota_stop_ratio

    def _cache(self, session: Session) -> BoardImageCache:
        return BoardImageCache(session, self._settings.board_image_ttl_days, self._settings.board_image_cache_limit)
