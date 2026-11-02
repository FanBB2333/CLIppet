"""Read Codex accounts from Cockpit Tools' local account store (read-only).

Cockpit Tools (https://github.com/jlcodes99/cockpit-tools) keeps one file per
account under ``<data dir>/codex_accounts/`` and an index in
``codex_accounts.json``.  Account files are sealed with AES-256-GCM using the
32-byte key stored base64-encoded in ``secure-account-storage.key``::

    {"version": 1, "kind": "codex", "algorithm": "AES-256-GCM",
     "key_id": "local-secure-account-storage-v1",
     "nonce": "<base64, 12 bytes>", "ciphertext": "<base64>", "encrypted_at": ...}

CLIppet only ever *reads* this store.  Cockpit keeps refreshing the tokens
it manages, so CLIppet envs that import from it are best kept *linked*
(``clippet env import --link``) to always pick up the newest login.

Decryption needs the optional ``cryptography`` package
(``pip install 'clippet[cockpit]'``).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from clippet.auth.codex import (
    FORMAT_COCKPIT_STORE,
    CodexCredential,
    CredentialFormatError,
    credential_from_object,
)

COCKPIT_DIR_ENV = "CLIPPET_COCKPIT_DIR"
DEFAULT_COCKPIT_DIRS = (".antigravity_cockpit", ".cockpit_tools")
INDEX_FILE = "codex_accounts.json"
ACCOUNTS_DIR = "codex_accounts"
KEY_FILE = "secure-account-storage.key"


def is_cockpit_dir(path: Path | str) -> bool:
    """Return True if *path* looks like a Cockpit Tools data directory."""

    path = Path(path)
    return (path / INDEX_FILE).is_file() or (
        (path / ACCOUNTS_DIR).is_dir() and (path / KEY_FILE).is_file()
    )


def find_cockpit_dir(explicit: str | Path | None = None) -> Path:
    """Locate the Cockpit Tools data directory.

    Order: explicit argument, ``$CLIPPET_COCKPIT_DIR``, ``~/.antigravity_cockpit``,
    ``~/.cockpit_tools``.
    """

    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_dir = os.environ.get(COCKPIT_DIR_ENV)
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.extend(Path.home() / name for name in DEFAULT_COCKPIT_DIRS)

    for candidate in candidates:
        if is_cockpit_dir(candidate):
            return candidate
    if explicit:
        raise FileNotFoundError(f"'{explicit}' is not a Cockpit Tools data directory.")
    raise FileNotFoundError(
        "Cockpit Tools data directory not found (looked for ~/.antigravity_cockpit "
        f"and ~/.cockpit_tools). Set {COCKPIT_DIR_ENV} or pass cockpit:<dir>."
    )


def _load_key(root: Path) -> bytes:
    key_path = root / KEY_FILE
    try:
        raw = key_path.read_bytes().strip()
    except OSError as exc:
        raise CredentialFormatError(f"Cannot read Cockpit key file {key_path}: {exc}") from exc
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:
        key = raw
    if len(key) != 32:
        raise CredentialFormatError(
            f"Unexpected Cockpit key length ({len(key)} bytes) in {key_path}."
        )
    return key


def decrypt_envelope(envelope: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Decrypt one Cockpit account envelope and return the account object."""

    algorithm = str(envelope.get("algorithm", "")).upper()
    if algorithm not in {"AES-256-GCM", "AES256GCM", "AES-GCM"}:
        raise CredentialFormatError(f"Unsupported Cockpit cipher: {envelope.get('algorithm')!r}")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on env
        raise CredentialFormatError(
            "Reading the Cockpit Tools account store needs the 'cryptography' "
            "package: pip install 'clippet[cockpit]' (or pip install cryptography). "
            "Alternatively export the accounts from Cockpit Tools and pass the "
            "exported JSON file."
        ) from exc

    nonce = base64.b64decode(envelope["nonce"])
    ciphertext = base64.b64decode(envelope["ciphertext"])
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:  # cryptography.exceptions.InvalidTag
        raise CredentialFormatError(
            "Could not decrypt a Cockpit account file (wrong key or unknown format)."
        ) from exc
    data = json.loads(plaintext.decode("utf-8"))
    if not isinstance(data, dict):
        raise CredentialFormatError("Decrypted Cockpit account is not a JSON object.")
    return data


def _read_account(path: Path, key: bytes | None) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(envelope, dict) and "ciphertext" in envelope:
        if key is None:
            raise CredentialFormatError("Cockpit key file is missing.")
        return decrypt_envelope(envelope, key)
    if not isinstance(envelope, dict):
        raise CredentialFormatError(f"Unexpected Cockpit account file: {path}")
    return envelope  # older Cockpit versions stored accounts in clear text


def load_store_credentials(explicit: str | Path | None = None) -> list[CodexCredential]:
    """Return every Codex account in the Cockpit Tools store, index order first."""

    root = find_cockpit_dir(explicit)
    accounts_dir = root / ACCOUNTS_DIR

    index: dict[str, Any] = {}
    index_path = root / INDEX_FILE
    if index_path.is_file():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                index = loaded
        except json.JSONDecodeError:
            index = {}

    ordered_ids: list[str] = []
    meta_by_id: dict[str, dict[str, Any]] = {}
    for entry in index.get("accounts") or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            ordered_ids.append(entry["id"])
            meta_by_id[entry["id"]] = entry
    if accounts_dir.is_dir():
        for path in sorted(accounts_dir.glob("*.json")):
            if path.stem not in meta_by_id:
                ordered_ids.append(path.stem)

    key = _load_key(root) if (root / KEY_FILE).is_file() else None
    current_id = index.get("current_account_id")

    credentials: list[CodexCredential] = []
    for account_id in ordered_ids:
        path = accounts_dir / f"{account_id}.json"
        if not path.is_file():
            continue
        account = _read_account(path, key)
        meta = meta_by_id.get(account_id, {})
        for field in ("email", "plan_type"):
            if field in meta and not account.get(field):
                account[field] = meta[field]
        credential = credential_from_object(
            account,
            source_path=str(root),
            source_format=FORMAT_COCKPIT_STORE,
            source_id=account_id,
        )
        if credential is None:
            continue
        credential.is_current = account_id == current_id
        credentials.append(credential)

    if not credentials:
        raise CredentialFormatError(f"No Codex accounts found in Cockpit Tools store {root}.")
    return credentials
