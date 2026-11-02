"""Tests for the Codex credential identity layer (clippet.auth)."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clippet.auth.codex import (
    FORMAT_CLIPROXYAPI,
    FORMAT_COCKPIT_EXPORT,
    FORMAT_COCKPIT_STORE,
    FORMAT_NATIVE,
    FORMAT_SUB2API,
    CodexCredential,
    CredentialFormatError,
    CredentialSelectionError,
    extract_codex_credentials,
    format_timestamp,
    load_codex_credentials,
    parse_timestamp,
    select_credential,
)
from clippet.auth.jwt import decode_jwt_claims
from tests.codex_fixtures import (
    cliproxyapi_file,
    cockpit_account,
    cockpit_export,
    native_auth,
    openai_tokens,
    sub2api_export,
)


def _write(tmp_path: Path, name: str, data) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestNativeAuthJson:
    def test_chatgpt_auth_json_round_trips_verbatim(self, tmp_path: Path) -> None:
        doc = native_auth()
        doc["tokens"]["oauth_provenance"] = {"issuer": "x", "client_id": "y"}
        creds = load_codex_credentials(_write(tmp_path, "auth.json", doc))

        assert len(creds) == 1
        cred = creds[0]
        assert cred.source_format == FORMAT_NATIVE
        assert cred.auth_mode == "chatgpt"
        assert cred.display_email == "alice@example.com"
        assert cred.display_plan == "plus"
        assert cred.display_account_id == "acct-alice"
        # Unknown/newer fields survive untouched
        assert cred.to_auth_json() == doc

    def test_api_key_auth_json(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "auth.json", {"OPENAI_API_KEY": "sk-test"})
        cred = load_codex_credentials(path)[0]
        assert cred.auth_mode == "apikey"
        assert cred.api_key == "sk-test"

    def test_personal_access_token_is_passed_through(self, tmp_path: Path) -> None:
        doc = {"OPENAI_API_KEY": None, "personal_access_token": "at-123"}
        cred = load_codex_credentials(_write(tmp_path, "auth.json", doc))[0]
        assert cred.auth_mode == "personal_access_token"
        assert cred.to_auth_json() == doc

    def test_logged_out_file_has_no_credentials(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "auth.json", {"OPENAI_API_KEY": None, "tokens": None})
        with pytest.raises(CredentialFormatError):
            load_codex_credentials(path)

    def test_home_and_codex_home_directories(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex" / "auth.json").write_text(json.dumps(native_auth()))
        assert load_codex_credentials(home)[0].display_email == "alice@example.com"
        assert load_codex_credentials(home / ".codex")[0].display_email == "alice@example.com"


class TestForeignFormats:
    def test_cockpit_export_only_yields_codex_accounts(self, tmp_path: Path) -> None:
        updated = int(datetime(2026, 10, 30, 3, 32, tzinfo=timezone.utc).timestamp())
        data = cockpit_export(
            cockpit_account("alice@example.com", "acct-alice", token_updated_at=updated),
            cockpit_account("bob@example.com", "acct-bob", requires_reauth=True),
        )
        creds = load_codex_credentials(_write(tmp_path, "backup.json", data))

        assert [c.display_email for c in creds] == ["alice@example.com", "bob@example.com"]
        assert all(c.source_format == FORMAT_COCKPIT_EXPORT for c in creds)
        assert creds[0].last_refresh == datetime(2026, 10, 30, 3, 32, tzinfo=timezone.utc)
        assert creds[0].source_id == "codex_acct-alice"
        assert creds[1].requires_reauth is True

        native = creds[0].to_auth_json()
        assert native["auth_mode"] == "chatgpt"
        assert native["OPENAI_API_KEY"] is None
        assert native["tokens"]["account_id"] == "acct-alice"
        assert native["last_refresh"] == "2026-10-30T03:32:00.000000Z"

    def test_cockpit_single_account_and_array(self, tmp_path: Path) -> None:
        one = cockpit_account("alice@example.com", "acct-alice")
        assert len(load_codex_credentials(_write(tmp_path, "one.json", one))) == 1
        two = [one, cockpit_account("bob@example.com", "acct-bob")]
        assert len(load_codex_credentials(_write(tmp_path, "two.json", two))) == 2

    def test_cockpit_api_key_account(self) -> None:
        entry = {
            "id": "codex_k", "email": "k@example.com", "auth_mode": "apikey",
            "openai_api_key": "sk-gw", "api_base_url": "https://gw.example.com/v1",
            "plan_type": "API_KEY", "tokens": {"id_token": "", "access_token": ""},
        }
        creds = extract_codex_credentials(cockpit_export(entry))
        assert len(creds) == 1
        assert creds[0].auth_mode == "apikey"
        assert creds[0].base_url == "https://gw.example.com/v1"
        assert creds[0].to_auth_json() == {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-gw"}

    def test_cockpit_web_session_accounts_are_skipped(self) -> None:
        entry = cockpit_account("w@example.com", "acct-w")
        entry["token_source_mode"] = "chatgpt_web_session"
        assert extract_codex_credentials(cockpit_export(entry)) == []

    def test_sub2api_export_skips_other_platforms(self, tmp_path: Path) -> None:
        creds = load_codex_credentials(_write(tmp_path, "sub2api.json", sub2api_export()))
        assert len(creds) == 1
        cred = creds[0]
        assert cred.source_format == FORMAT_SUB2API
        assert cred.label == "team-alice"
        assert cred.display_email == "alice@example.com"
        assert cred.plan_type == "team"
        assert cred.display_account_id == "acct-alice"

    def test_cliproxyapi_file(self, tmp_path: Path) -> None:
        cred = load_codex_credentials(_write(tmp_path, "codex-bob.json", cliproxyapi_file()))[0]
        assert cred.source_format == FORMAT_CLIPROXYAPI
        assert cred.display_email == "bob@example.com"
        assert cred.last_refresh == datetime(2026, 11, 2, 8, 4, 5, tzinfo=timezone.utc)
        # The separately issued "siwc" token set is never used
        assert cred.access_token != "other-client-token"

    def test_cliproxyapi_claude_file_is_not_codex(self) -> None:
        data = {"type": "claude", "access_token": "sk-ant-oat01-x", "refresh_token": "y"}
        assert extract_codex_credentials(data) == []

    def test_non_openai_tokens_are_rejected(self) -> None:
        assert extract_codex_credentials({"access_token": "ya29.google", "refresh_token": "x"}) == []

    def test_generic_openai_token_bundle(self) -> None:
        creds = extract_codex_credentials(openai_tokens("carol@example.com", "acct-carol"))
        assert len(creds) == 1
        assert creds[0].display_email == "carol@example.com"

    def test_chatgpt_web_session_dump_is_skipped(self) -> None:
        session = {
            "user": {"email": "w@example.com"},
            "expires": "2026-12-01T00:00:00Z",
            "accessToken": openai_tokens()["access_token"],
            "sessionToken": "secret",
        }
        assert extract_codex_credentials(session) == []

    def test_json_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "accounts.jsonl"
        path.write_text(
            json.dumps(cliproxyapi_file("a@example.com", "acct-a")) + "\n"
            + json.dumps(cliproxyapi_file("b@example.com", "acct-b")) + "\n"
        )
        assert [c.display_email for c in load_codex_credentials(path)] == [
            "a@example.com",
            "b@example.com",
        ]

    def test_garbage_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "x.json"
        path.write_text("not json at all")
        with pytest.raises(CredentialFormatError):
            load_codex_credentials(path)


class TestRendering:
    def test_missing_id_token_is_synthesized_as_valid_jwt(self) -> None:
        tokens = openai_tokens("dave@example.com", "acct-dave")
        del tokens["id_token"]
        cred = extract_codex_credentials(cockpit_export(
            {"id": "codex_d", "email": "dave@example.com", "auth_mode": "oauth",
             "account_id": "acct-dave", "tokens": tokens}
        ))[0]

        id_token = cred.to_auth_json()["tokens"]["id_token"]
        parts = id_token.split(".")
        assert len(parts) == 3 and all(parts)  # Codex rejects empty segments
        claims = decode_jwt_claims(id_token)
        assert claims["email"] == "dave@example.com"
        assert claims["https://api.openai.com/auth"]["chatgpt_account_id"] == "acct-dave"

    def test_last_refresh_falls_back_to_access_token_iat(self) -> None:
        issued = 1_790_000_000
        tokens = openai_tokens(issued_at=issued)
        cred = extract_codex_credentials(tokens)[0]
        assert cred.effective_last_refresh == datetime.fromtimestamp(issued, tz=timezone.utc)

    def test_repr_never_contains_tokens(self) -> None:
        cred = extract_codex_credentials(native_auth())[0]
        text = repr(cred)
        assert cred.refresh_token not in text
        assert cred.access_token not in text

    def test_summary_has_no_secrets(self) -> None:
        cred = extract_codex_credentials(native_auth())[0]
        dumped = json.dumps(cred.summary())
        assert cred.refresh_token not in dumped
        assert cred.access_token not in dumped

    @pytest.mark.parametrize(
        "value, expected",
        [
            (1_790_000_000, datetime.fromtimestamp(1_790_000_000, tz=timezone.utc)),
            (1_790_000_000_000, datetime.fromtimestamp(1_790_000_000, tz=timezone.utc)),
            ("2026-11-02T08:31:14.123456789Z", datetime(2026, 11, 2, 8, 31, 14, 123456, tzinfo=timezone.utc)),
            ("2026-11-02T16:04:05+08:00", datetime(2026, 11, 2, 8, 4, 5, tzinfo=timezone.utc)),
            ("", None),
            (None, None),
        ],
    )
    def test_parse_timestamp(self, value, expected) -> None:
        assert parse_timestamp(value) == expected

    def test_format_timestamp(self) -> None:
        moment = datetime(2026, 11, 2, 8, 31, 14, 5, tzinfo=timezone.utc)
        assert format_timestamp(moment) == "2026-11-02T08:31:14.000005Z"


class TestSelection:
    def _creds(self) -> list[CodexCredential]:
        return extract_codex_credentials(cockpit_export(
            cockpit_account("alice@example.com", "acct-alice"),
            cockpit_account("bob@corp.example", "acct-bob"),
        ))

    def test_by_index_email_local_part_and_id(self) -> None:
        creds = self._creds()
        assert select_credential(creds, "2").display_email == "bob@corp.example"
        assert select_credential(creds, "ALICE@example.com").display_email == "alice@example.com"
        assert select_credential(creds, "bob").display_email == "bob@corp.example"
        assert select_credential(creds, "acct-al").display_email == "alice@example.com"
        assert select_credential(creds, "codex_acct-bob").display_email == "bob@corp.example"

    def test_multiple_without_selector_lists_choices(self) -> None:
        with pytest.raises(CredentialSelectionError) as exc:
            select_credential(self._creds())
        assert "alice@example.com" in str(exc.value)
        assert "--account" in str(exc.value)

    def test_single_usable_account_is_picked_automatically(self) -> None:
        creds = extract_codex_credentials(cockpit_export(
            cockpit_account("alice@example.com", "acct-alice", requires_reauth=True),
            cockpit_account("bob@example.com", "acct-bob"),
        ))
        assert select_credential(creds).display_email == "bob@example.com"

    def test_ambiguous_and_unknown(self) -> None:
        creds = self._creds()
        with pytest.raises(CredentialSelectionError, match="several"):
            select_credential(creds, "acct-")
        with pytest.raises(CredentialSelectionError, match="No Codex account"):
            select_credential(creds, "nobody")
        with pytest.raises(CredentialSelectionError, match="out of range"):
            select_credential(creds, "9")


class TestCockpitStore:
    @pytest.fixture()
    def store(self, tmp_path: Path) -> Path:
        aesgcm = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
        key = os.urandom(32)
        root = tmp_path / ".antigravity_cockpit"
        (root / "codex_accounts").mkdir(parents=True)
        (root / "secure-account-storage.key").write_text(base64.b64encode(key).decode())

        accounts = [
            cockpit_account("alice@example.com", "acct-alice", cockpit_id="codex_aaa"),
            cockpit_account("bob@example.com", "acct-bob", cockpit_id="codex_bbb"),
        ]
        for account in accounts:
            nonce = os.urandom(12)
            ciphertext = aesgcm.AESGCM(key).encrypt(nonce, json.dumps(account).encode(), None)
            envelope = {
                "version": 1, "kind": "codex", "algorithm": "AES-256-GCM",
                "key_id": "local-secure-account-storage-v1",
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "encrypted_at": 1_790_000_000,
            }
            (root / "codex_accounts" / f"{account['id']}.json").write_text(json.dumps(envelope))
        (root / "codex_accounts.json").write_text(json.dumps({
            "version": "1.0",
            "accounts": [{"id": "codex_bbb", "email": "bob@example.com"},
                         {"id": "codex_aaa", "email": "alice@example.com"}],
            "current_account_id": "codex_aaa",
        }))
        return root

    def test_reads_encrypted_store_in_index_order(self, store: Path) -> None:
        creds = load_codex_credentials(f"cockpit:{store}")
        assert [c.display_email for c in creds] == ["bob@example.com", "alice@example.com"]
        assert all(c.source_format == FORMAT_COCKPIT_STORE for c in creds)
        assert [c.is_current for c in creds] == [False, True]
        assert creds[1].source_id == "codex_aaa"

    def test_directory_path_is_recognized(self, store: Path) -> None:
        assert len(load_codex_credentials(store)) == 2

    def test_env_var_locates_store(self, store: Path, monkeypatch) -> None:
        monkeypatch.setenv("CLIPPET_COCKPIT_DIR", str(store))
        assert len(load_codex_credentials("cockpit")) == 2

    def test_wrong_key_fails_cleanly(self, store: Path) -> None:
        (store / "secure-account-storage.key").write_text(base64.b64encode(b"\0" * 32).decode())
        with pytest.raises(CredentialFormatError, match="decrypt"):
            load_codex_credentials(store)
