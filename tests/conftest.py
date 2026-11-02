"""Fixtures shared by several test modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clippet.auth.codex import load_codex_credentials
from clippet.config.environments import create_home_env, import_codex_credential
from tests.codex_fixtures import cockpit_account, cockpit_export


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A fake HOME and CLIppet home with two imported Codex accounts and one empty env."""

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model = "gpt-5.5"\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLIPPET_HOME", str(tmp_path / "clippet"))
    for name in ("CLIPPET_COCKPIT_DIR", "CODEX_HOME", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    export = tmp_path / "cockpit_auto_backup.json"
    export.write_text(json.dumps(cockpit_export(
        cockpit_account("alice@example.com", "acct-alice"),
        cockpit_account("bob@example.com", "acct-bob", plan="pro"),
        cockpit_account("carol@example.com", "acct-carol", requires_reauth=True),
    )))
    creds = load_codex_credentials(export)
    import_codex_credential("alice", creds[0], source=str(export), link=True)
    import_codex_credential("bob", creds[1], source=str(export))
    create_home_env("scratch")
    work = tmp_path / "work"
    work.mkdir()
    return {"home": home, "export": export, "work": work, "tmp": tmp_path}
