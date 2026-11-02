"""Synthetic Codex credentials for tests (no real tokens anywhere)."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

OPENAI_AUTH = "https://api.openai.com/auth"
OPENAI_PROFILE = "https://api.openai.com/profile"


def _b64(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def fake_jwt(claims: dict[str, Any]) -> str:
    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(claims)}.c2lnbmF0dXJl"


def openai_tokens(
    email: str = "alice@example.com",
    account_id: str = "acct-alice",
    plan: str = "plus",
    issued_at: int | None = None,
    lifetime: int = 10 * 86400,
) -> dict[str, str]:
    """Return id/access/refresh tokens shaped like OpenAI's."""

    iat = int(time.time()) - 3600 if issued_at is None else issued_at
    auth = {
        "chatgpt_account_id": account_id,
        "chatgpt_plan_type": plan,
        "user_id": f"user-{account_id}",
    }
    id_token = fake_jwt({
        "iss": "https://auth.openai.com",
        "email": email,
        OPENAI_AUTH: auth,
        "iat": iat,
        "exp": iat + 3600,
    })
    access_token = fake_jwt({
        "iss": "https://auth.openai.com",
        "aud": ["https://api.openai.com/v1"],
        OPENAI_AUTH: auth,
        OPENAI_PROFILE: {"email": email},
        "iat": iat,
        "exp": iat + lifetime,
    })
    return {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": f"rt_{account_id}_{iat}",
    }


def native_auth(email: str = "alice@example.com", account_id: str = "acct-alice", **kw) -> dict:
    tokens = openai_tokens(email, account_id, **kw)
    tokens["account_id"] = account_id
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": tokens,
        "last_refresh": "2026-10-30T03:32:12.123456Z",
    }


def cockpit_quota(*windows: tuple[float, int, int], checked_at: int | None = None) -> dict:
    """A Cockpit Tools ``quota`` block; each window is (used %, window minutes, seconds until it resets)."""

    now = checked_at or int(time.time())
    raw = [
        {"used_percent": used, "limit_window_seconds": minutes * 60, "reset_after_seconds": after,
         "reset_at": now + after}
        for used, minutes, after in windows
    ]
    quota: dict[str, Any] = {"raw_data": {"plan_type": "plus", "rate_limit": {
        "allowed": True, "limit_reached": False,
        "primary_window": raw[0] if raw else None,
        "secondary_window": raw[1] if len(raw) > 1 else None,
    }}}
    for prefix, window in zip(("hourly", "weekly"), raw):  # Cockpit's summary: the share left
        quota[f"{prefix}_percentage"] = 100 - window["used_percent"]
        quota[f"{prefix}_window_minutes"] = window["limit_window_seconds"] // 60
        quota[f"{prefix}_reset_time"] = window["reset_at"]
        quota[f"{prefix}_window_present"] = True
    return quota


def cockpit_account(
    email: str,
    account_id: str,
    *,
    cockpit_id: str | None = None,
    requires_reauth: bool | None = None,
    token_updated_at: int | None = None,
    quota: dict | None = None,
    usage_updated_at: int | None = None,
    **kw,
) -> dict:
    tokens = openai_tokens(email, account_id, **kw)
    entry = {
        "id": cockpit_id or f"codex_{account_id}",
        "email": email,
        "auth_mode": "oauth",
        "api_provider_mode": "openai_builtin",
        "plan_type": "plus",
        "account_id": account_id,
        "tokens": tokens,
        "token_generation": 3,
        "token_updated_at": token_updated_at or int(time.time()) - 7200,
        "token_source_mode": "managed",
        "quota": quota if quota is not None else {"hourly_percentage": 10},
    }
    if usage_updated_at is not None:
        entry["usage_updated_at"] = usage_updated_at
    if requires_reauth is not None:
        entry["requires_reauth"] = requires_reauth
        entry["reauth_reason"] = "revoked" if requires_reauth else None
    return entry


def cockpit_export(*accounts: dict) -> dict:
    return {
        "schema": "cockpit-tools.data-transfer",
        "version": 1,
        "exported_at": "2026-11-02T03:10:12.000Z",
        "sections": {"accounts": True, "config": True},
        "accounts": {
            "schema": "cockpit-tools.account-transfer",
            "version": 1,
            "platforms": {
                "claude_manager": {
                    "account_count": 1,
                    "exported_data": [
                        {"id": "claude_1", "email": "c@example.com",
                         "tokens": {"access_token": "sk-ant-oat-x", "refresh_token": "r"}}
                    ],
                },
                "codex": {"account_count": len(accounts), "exported_data": list(accounts)},
            },
        },
    }


def sub2api_export() -> dict:
    alice = openai_tokens("alice@example.com", "acct-alice")
    return {
        "exported_at": "2026-11-02T10:00:00+08:00",
        "proxies": [],
        "accounts": [
            {
                "name": "team-alice",
                "platform": "openai",
                "type": "oauth",
                "credentials": {
                    **alice,
                    "expires_at": "2026-11-12T10:00:00+08:00",
                    "email": "alice@example.com",
                    "chatgpt_account_id": "acct-alice",
                    "plan_type": "team",
                    "client_id": "app_x",
                },
                "extra": {},
                "concurrency": 3,
                "priority": 50,
            },
            {
                "name": "claude-pool",
                "platform": "anthropic",
                "type": "oauth",
                "credentials": {"access_token": "sk-ant-oat01-x", "refresh_token": "sk-ant-ort01-y"},
            },
        ],
    }


def cliproxyapi_file(email: str = "bob@example.com", account_id: str = "acct-bob") -> dict:
    tokens = openai_tokens(email, account_id)
    return {
        **tokens,
        "account_id": account_id,
        "last_refresh": "2026-11-02T16:04:05+08:00",
        "email": email,
        "type": "codex",
        "expired": "2026-11-12T16:04:05+08:00",
        "plan_type": "pro",
        "siwc": {"access_token": "other-client-token", "refresh_token": "x"},
    }
