"""Minimal, dependency-free JWT helpers.

CLIppet only *reads* claims from tokens it already holds (to show which
account a credential belongs to, when it expires, ...).  Signatures are never
verified and tokens are never sent anywhere from here.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt_claims(token: object) -> dict[str, Any] | None:
    """Return the (unverified) payload of a JWT, or *None* if it is not one."""

    if not isinstance(token, str) or token.count(".") != 2:
        return None
    try:
        payload = json.loads(_b64url_decode(token.split(".")[1]))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def encode_unsigned_jwt(claims: dict[str, Any]) -> str:
    """Build an unsigned (``alg: none``) JWT carrying *claims*.

    Only used to give Codex CLI an ``id_token`` it can parse for display
    purposes when a credential source stored nothing but an access token.
    """

    def _segment(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    # Codex requires all three segments to be non-empty base64url.
    signature = base64.urlsafe_b64encode(b"clippet-unsigned").rstrip(b"=").decode("ascii")
    return f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(claims)}.{signature}"


def claim_time(claims: dict[str, Any] | None, key: str) -> datetime | None:
    """Return a numeric JWT time claim (``exp`` / ``iat`` / ...) as aware UTC."""

    if not claims:
        return None
    value = claims.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
