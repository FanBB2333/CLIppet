"""Antigravity accounts in Cockpit Tools and agy's portable OAuth file."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clippet.auth import cockpit
from clippet.auth.codex import CredentialFormatError, CredentialSelectionError, format_timestamp, parse_timestamp
from clippet.auth.jwt import decode_jwt_claims

TOKEN_PATH = ".gemini/antigravity-cli/antigravity-oauth-token"


def file_auth_env() -> dict[str, str]:
    """Select agy's file-backed OAuth storage, including on desktop machines.

    agy 1.5 checks for a nonempty SSH_CLIENT when choosing its token store.
    The marker affects this child process and its descendants only.
    """
    return {"SSH_CLIENT": os.environ.get("SSH_CLIENT") or "clippet-file-auth"}


def login_email(document: dict) -> str | None:
    claims = decode_jwt_claims(document.get("id_token")) or {}
    email = claims.get("email")
    return email if isinstance(email, str) and email else None


def check_bound_login(home: Path, expected_email: str, run_env: dict[str, str]) -> None:
    """Catch missing credentials and explicit provider overrides before starting agy."""
    path = home / TOKEN_PATH
    if not path.is_file():
        raise ValueError("The imported agy login file is missing. Import the account again.")
    document = json.loads(path.read_text(encoding="utf-8"))
    token = document.get("token") if isinstance(document, dict) else None
    if not isinstance(token, dict) or not token.get("access_token") or not token.get("refresh_token"):
        raise CredentialFormatError("The env's agy login is incomplete. Import the account again.")
    email = login_email(document)
    if email and email.casefold() != expected_email.casefold():
        raise CredentialFormatError("The env's agy login belongs to a different account.")
    settings_path = path.with_name("settings.json")
    if settings_path.is_file():
        config = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(config, dict) and config.get("modelProvider") in ("gemini", "gateway"):
            raise ValueError("This env selects a different agy modelProvider. Use a separate env for its Cockpit login.")
    shared_config = home / ".gemini" / "config" / "config.json"
    shared = json.loads(shared_config.read_text(encoding="utf-8")) if shared_config.is_file() else {}
    settings = shared.get("userSettings") if isinstance(shared, dict) else None
    adc_enabled = isinstance(settings, dict) and settings.get("enableAdc") is True
    if adc_enabled or run_env.get("AGY_ADC_AUTH", "").lower() in ("1", "true"):
        raise ValueError("This env selects Google ADC credentials. Use a separate env for its Cockpit login.")


@dataclass(frozen=True)
class AgyAccount:
    root: Path
    account_id: str
    email: str
    is_current: bool

    def native_login(self) -> dict[str, Any]:
        """Decrypt just this account and render the credential agy can refresh."""

        accounts_dir = self.root / "accounts"
        path = accounts_dir / f"{self.account_id}.json"
        if not path.resolve().is_relative_to(accounts_dir.resolve()):
            raise CredentialFormatError("Cockpit account file points outside the account directory.")
        key = cockpit._load_key(self.root) if (self.root / cockpit.KEY_FILE).is_file() else None
        try:
            data = cockpit._read_account(path, key)
        except (KeyError, TypeError) as exc:
            raise CredentialFormatError(f"Malformed Cockpit account file for {self.email}.") from exc
        if data.get("disabled") or data.get("pending_oauth"):
            raise CredentialFormatError(f"Cockpit marks {self.email} as disabled or pending authorization.")
        token = data.get("token")
        if not isinstance(token, dict) or not all(
            isinstance(token.get(k), str) and token[k].strip() for k in ("access_token", "refresh_token")
        ):
            raise CredentialFormatError(f"Cockpit has no reusable agy login for {self.email}.")
        emails = (data.get("email"), token.get("email"), login_email(token))
        if data.get("id", self.account_id) != self.account_id or any(
            email and (not isinstance(email, str) or email.casefold() != self.email.casefold())
            for email in emails
        ):
            raise CredentialFormatError("Cockpit account details and index refer to different identities.")
        expiry = parse_timestamp(token.get("expiry_timestamp"))
        if expiry is None:
            raise CredentialFormatError(f"Missing or invalid agy token expiry for {self.email}.")
        result: dict[str, Any] = {
            "token": {
                "access_token": token["access_token"],
                "refresh_token": token["refresh_token"],
                "token_type": "Bearer",
                "expiry": format_timestamp(expiry),
            },
            "auth_method": "consumer",
        }
        if isinstance(token.get("id_token"), str) and token["id_token"]:
            result["id_token"] = token["id_token"]
        return result


def _index(root: Path) -> tuple[list[tuple], str | None]:
    database = root / "antigravity_accounts.sqlite"
    if database.is_file():
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                # Cockpit stages new generations in this table. Read the active
                # generation and its rows together, in the order shown by Cockpit.
                db.execute("BEGIN")
                row = db.execute("SELECT state FROM account_index_state WHERE singleton=1").fetchone()
                state = json.loads(row[0]) if row else {}
                if not isinstance(state, dict):
                    raise ValueError("Invalid Cockpit index state.")
                if state.get("complete"):
                    rows = db.execute(
                        "SELECT account_id, email FROM summaries WHERE source=? ORDER BY position",
                        (state["source"],),
                    ).fetchall()
                    return rows, state.get("current_id")
        except (sqlite3.Error, KeyError, ValueError, TypeError) as exc:
            raise CredentialFormatError(f"Cannot read the Cockpit agy account index at {database}.") from exc
    index = json.loads((root / "accounts.json").read_text(encoding="utf-8"))
    if not isinstance(index, dict) or not isinstance(index.get("accounts"), list):
        raise CredentialFormatError("Malformed Cockpit agy accounts.json index.")
    rows = [(row.get("id"), row.get("email")) for row in index["accounts"] if isinstance(row, dict)]
    return rows, index.get("current_account_id")


def list_agy_accounts(source: str | Path) -> list[AgyAccount]:
    """Read only account metadata, without opening a credential or its key."""

    text = str(source)
    explicit = None if text == "cockpit" else text.removeprefix("cockpit:")
    root = cockpit.find_cockpit_dir(explicit, agent="agy").resolve()
    rows, current = _index(root)
    accounts = []
    for account_id, email in rows:
        if (not isinstance(account_id, str) or not account_id or account_id in (".", "..")
                or any(c in account_id for c in "/\\:\x00") or not isinstance(email, str) or not email):
            raise CredentialFormatError("Invalid account ID or email in Cockpit agy index.")
        accounts.append(AgyAccount(root, account_id, email, account_id == current))
    return accounts


def select_agy_account(source: str | Path, selector: str | int | None) -> AgyAccount:
    accounts = list_agy_accounts(source)
    text = str(selector).strip().casefold() if selector is not None else ""
    if not accounts:
        raise CredentialSelectionError("No agy accounts in this Cockpit store.")
    if text.isdecimal():
        index = int(text)
        if 1 <= index <= len(accounts):
            return accounts[index - 1]
        raise CredentialSelectionError(f"agy account index must be between 1 and {len(accounts)}.")
    if not text and len(accounts) == 1:
        return accounts[0]
    matches = [a for a in accounts if text in (
        a.account_id.casefold(), a.email.casefold(), a.email.casefold().partition("@")[0],
    )]
    if len(matches) == 1:
        return matches[0]
    choices = ", ".join(f"{i}. {a.email}" for i, a in enumerate(accounts, 1))
    raise CredentialSelectionError(f"Select one agy account with --account (index, email or ID): {choices}")
