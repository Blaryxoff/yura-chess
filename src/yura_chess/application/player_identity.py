"""Pseudonymous owner keys.

Alice identifiers never reach the database. Every identifier is turned into an
HMAC digest under a server-side salt, so a leaked database cannot be linked back
to Alice accounts and a stolen digest cannot be recomputed without the salt.
"""

from __future__ import annotations

import hmac
from hashlib import sha256

from pydantic import SecretStr

from yura_chess.storage.models import OWNER_KEY_LENGTH


class UnidentifiedRequestError(ValueError):
    """The request carries neither a user nor an application identifier."""


def owner_key(salt: SecretStr, user_id: str | None, application_id: str | None) -> str:
    """Derive the owner key, preferring the account over the device.

    `application_id` identifies an installation rather than a person, so it is
    used only when Alice sends no `user_id` at all; the scope prefix keeps the
    two namespaces from ever colliding.
    """
    if user_id:
        scoped = f"user:{user_id}"
    elif application_id:
        scoped = f"application:{application_id}"
    else:
        raise UnidentifiedRequestError("request carries no user or application identifier")
    digest = hmac.new(salt.get_secret_value().encode("utf-8"), scoped.encode("utf-8"), sha256).hexdigest()
    return digest[:OWNER_KEY_LENGTH]
