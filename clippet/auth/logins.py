"""Who an agent is logged in as in a HOME, read from its own files.

Only names are read (an e-mail, a GitHub login, the providers pi has keys
for), never a token or key.  Anything unexpected reads as "unknown", since
these files are the agents' own state and change between versions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # JSON with // comments (Copilot CLI's config.json)
    try:
        return json.loads(re.sub(r"(?m)^\s*//.*$", "", text))
    except json.JSONDecodeError:
        return None


def _claude(home: Path) -> str | None:
    data = _json(home / ".claude.json")
    account = data.get("oauthAccount") if isinstance(data, dict) else None
    if isinstance(account, dict) and isinstance(account.get("emailAddress"), str):
        org = account.get("organizationName")
        return account["emailAddress"] + (f" ({org})" if isinstance(org, str) and org else "")
    if (home / ".claude" / ".credentials.json").is_file():
        return "logged in"
    return None


def _copilot(home: Path) -> str | None:
    data = _json(home / ".copilot" / "config.json")
    if not isinstance(data, dict):
        return None
    for key in ("lastLoggedInUser", "last_logged_in_user"):
        user = data.get(key)
        if isinstance(user, dict) and isinstance(user.get("login"), str):
            host = str(user.get("host") or "")
            return user["login"] + ("" if not host or "github.com" in host else f" @ {host}")
    return None


def _gemini(home: Path) -> str | None:
    data = _json(home / ".gemini" / "google_accounts.json")
    if isinstance(data, dict) and isinstance(data.get("active"), str) and data["active"]:
        return data["active"]
    return "logged in" if (home / ".gemini" / "oauth_creds.json").is_file() else None


def _pi(home: Path) -> str | None:
    data = _json(home / ".pi" / "agent" / "auth.json")
    if not isinstance(data, dict) or not data:
        return None
    return "logged in to " + ", ".join(sorted(str(provider) for provider in data))


def _agy(home: Path) -> str | None:
    return "signed in" if (home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").is_file() else None


def _qoder(home: Path) -> str | None:
    auth = home / ".qoder" / ".auth"
    try:
        return "logged in" if auth.is_dir() and any(auth.iterdir()) else None
    except OSError:
        return None


_READERS = {
    "claude": _claude,
    "copilot": _copilot,
    "gemini": _gemini,
    "pi": _pi,
    "agy": _agy,
    "qodercli": _qoder,
}


def login_of(home: Path | str | None, agent: str) -> str | None:
    """Who *agent* is logged in as in *home*, as far as its files say (None: not that we can see)."""

    reader = _READERS.get(agent)
    if home is None or reader is None:
        return None
    try:
        return reader(Path(home))
    except Exception:  # noqa: BLE001 - another program's state; never break a view over it
        return None
