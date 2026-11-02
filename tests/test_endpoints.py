"""Third-party API endpoints for the agents in an env (clippet.endpoints)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+

import clippet.config.environments as env_mod
from clippet.config.detector import create_adapter_with_second_home
from clippet.config.environments import (
    clone_home_env,
    create_home_env,
    create_home_env_with_report,
    env_vars,
    get_environment,
    import_codex_credential,
    read_env_codex_credential,
    set_env_vars,
)
from clippet.endpoints import (
    MissingVariable,
    clear_endpoint,
    endpoint_of,
    resolve_vars,
    run_vars,
    set_codex_base_url,
    set_endpoint,
    shown,
)

KEY = "sk-test-not-a-real-key"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_mod, "get_clippet_root", lambda: tmp_path / ".clippet")
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: real_home))


@pytest.fixture
def home() -> Path:
    return create_home_env("relay")


class TestVariables:
    def test_references_are_resolved_from_our_environment(self) -> None:
        values = {"ANTHROPIC_AUTH_TOKEN": "${RELAY_KEY}", "ANTHROPIC_BASE_URL": "https://relay.example.com"}

        assert resolve_vars(values, {"RELAY_KEY": KEY}) == {
            "ANTHROPIC_AUTH_TOKEN": KEY,
            "ANTHROPIC_BASE_URL": "https://relay.example.com",
        }

    def test_an_unset_reference_says_which_and_where(self) -> None:
        with pytest.raises(MissingVariable, match=r"env 'relay' \(claude\) takes \$RELAY_KEY"):
            resolve_vars({"ANTHROPIC_AUTH_TOKEN": "${RELAY_KEY}"}, {}, where="env 'relay' (claude)")

    def test_keys_are_shown_only_as_references(self) -> None:
        assert shown("ANTHROPIC_AUTH_TOKEN", "${RELAY_KEY}") == "${RELAY_KEY}"
        assert shown("ANTHROPIC_AUTH_TOKEN", KEY) == "(set)"
        assert shown("ANTHROPIC_BASE_URL", "https://relay.example.com") == "https://relay.example.com"

    def test_vars_are_kept_per_agent(self, home: Path) -> None:
        set_env_vars("relay", "claude", {"A": "1", "B": "2"})
        set_env_vars("relay", "pi", {"C": "3"})
        set_env_vars("relay", "claude", unset=["A"])

        profile = get_environment("relay")
        assert env_vars(profile, "claude") == {"B": "2"}
        assert env_vars(profile, "pi") == {"C": "3"}
        set_env_vars("relay", "claude", unset=["B"])
        set_env_vars("relay", "pi", unset=["C"])
        assert "vars" not in get_environment("relay")

    def test_an_env_run_gets_them_after_the_inherited_ones_are_dropped(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_endpoint("relay", "claude", "https://relay.example.com/anthropic", key_env="RELAY_KEY", model="kimi-k2")
        monkeypatch.setenv("RELAY_KEY", KEY)

        adapter = create_adapter_with_second_home(home, "claude")

        overrides = adapter.default_isolation.env_overrides
        assert overrides["ANTHROPIC_BASE_URL"] == "https://relay.example.com/anthropic"
        assert overrides["ANTHROPIC_AUTH_TOKEN"] == KEY
        assert overrides["ANTHROPIC_MODEL"] == "kimi-k2"
        assert overrides["DISABLE_AUTOUPDATER"] == "1"
        # applied after CLIppet drops the parent shell's ANTHROPIC_* variables
        assert adapter.default_isolation.scrub_agent_env

    def test_a_launch_without_the_key_stops_with_why(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        set_endpoint("relay", "claude", "https://relay.example.com/anthropic", key_env="RELAY_KEY")
        monkeypatch.delenv("RELAY_KEY", raising=False)

        with pytest.raises(MissingVariable, match=r"\$RELAY_KEY"):
            run_vars(home, "claude")

    def test_other_agents_and_unregistered_homes_get_nothing(self, home: Path, tmp_path: Path) -> None:
        set_env_vars("relay", "claude", {"ANTHROPIC_BASE_URL": "https://relay.example.com"})

        assert run_vars(home, "codex") == {}
        assert run_vars(tmp_path / "elsewhere", "claude") == {}


class TestEndpoints:
    def test_claude_takes_variables(self, home: Path) -> None:
        notes = set_endpoint("relay", "claude", "https://relay.example.com/anthropic", key_env="RELAY_KEY")

        assert env_vars(get_environment("relay"), "claude") == {
            "ANTHROPIC_AUTH_TOKEN": "${RELAY_KEY}",
            "ANTHROPIC_BASE_URL": "https://relay.example.com/anthropic",
        }
        assert any("Messages API" in note for note in notes)
        found = endpoint_of(home, "claude")
        assert (found.url, found.key, found.source) == ("https://relay.example.com/anthropic", "$RELAY_KEY", "env vars")

    def test_a_new_endpoint_replaces_the_old_ones_variables(self, home: Path) -> None:
        set_endpoint("relay", "copilot", "https://a.example.com/v1", api="openai-responses", key_env="A", model="m")
        set_endpoint("relay", "copilot", "https://b.example.com", api="anthropic", model="claude-x")

        assert env_vars(get_environment("relay"), "copilot") == {
            "COPILOT_MODEL": "claude-x",
            "COPILOT_PROVIDER_BASE_URL": "https://b.example.com",
            "COPILOT_PROVIDER_TYPE": "anthropic",
        }

    def test_claude_settings_env_block_is_reported_as_winning(self, home: Path) -> None:
        (home / ".claude").mkdir()
        (home / ".claude" / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://x"}}))

        notes = set_endpoint("relay", "claude", "https://relay.example.com/anthropic")

        assert any("settings.json sets ANTHROPIC_BASE_URL" in note for note in notes)

    def test_codex_gets_a_provider_in_config_toml(self, home: Path) -> None:
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text(
            '# mine\nmodel = "gpt-old"\nmodel_provider = "other"\napproval_policy = "never"\n\n'
            '[model_providers.other]\nbase_url = "https://other.example.com"\n\n'
            '[profiles.fast]\nmodel = "gpt-mini"\n'
        )

        set_endpoint("relay", "codex", "https://relay.example.com/v1", key_env="RELAY_KEY", model="gpt-5.5")

        text = (home / ".codex" / "config.toml").read_text()
        config = tomllib.loads(text)
        assert text.startswith("# mine\n")
        assert config["model_provider"] == "clippet"
        assert config["model"] == "gpt-5.5"
        assert config["approval_policy"] == "never"
        assert config["profiles"]["fast"]["model"] == "gpt-mini"
        assert config["model_providers"]["other"]["base_url"] == "https://other.example.com"
        assert config["model_providers"]["clippet"] == {
            "name": "relay.example.com",
            "base_url": "https://relay.example.com/v1",
            "env_key": "CLIPPET_CODEX_API_KEY",
            "wire_api": "responses",
        }
        assert env_vars(get_environment("relay"), "codex") == {"CLIPPET_CODEX_API_KEY": "${RELAY_KEY}"}
        assert endpoint_of(home, "codex").key == "$RELAY_KEY"

        clear_endpoint("relay", "codex")

        config = tomllib.loads((home / ".codex" / "config.toml").read_text())
        assert "model_provider" not in config
        assert set(config["model_providers"]) == {"other"}
        assert env_vars(get_environment("relay"), "codex") == {}

    def test_codex_config_that_is_not_toml_is_left_alone(self, home: Path) -> None:
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text("model = \n")

        with pytest.raises(ValueError, match="edit it by hand"):
            set_endpoint("relay", "codex", "https://relay.example.com/v1")
        assert (home / ".codex" / "config.toml").read_text() == "model = \n"

    def test_pi_gets_a_provider_in_models_json(self, home: Path) -> None:
        set_endpoint("relay", "pi", "http://127.0.0.1:11434/v1", model="qwen3:8b")

        models = json.loads((home / ".pi" / "agent" / "models.json").read_text())
        settings = json.loads((home / ".pi" / "agent" / "settings.json").read_text())
        assert models["providers"]["clippet"]["api"] == "openai-completions"
        assert models["providers"]["clippet"]["models"] == [{"id": "qwen3:8b"}]
        assert settings == {"defaultProvider": "clippet", "defaultModel": "qwen3:8b"}
        assert endpoint_of(home, "pi").key is None

        set_endpoint("relay", "pi", "https://relay.example.com", api="anthropic", key_env="RELAY_KEY", model="m")
        provider = json.loads((home / ".pi" / "agent" / "models.json").read_text())["providers"]["clippet"]
        assert (provider["api"], provider["apiKey"]) == ("anthropic-messages", "$CLIPPET_PI_API_KEY")
        assert endpoint_of(home, "pi").key == "$RELAY_KEY"

        clear_endpoint("relay", "pi")
        assert json.loads((home / ".pi" / "agent" / "models.json").read_text()) == {"providers": {}}
        assert json.loads((home / ".pi" / "agent" / "settings.json").read_text()) == {}

    def test_antigravity_needs_its_api_key_provider_switched_on(self, home: Path) -> None:
        set_endpoint("relay", "agy", "https://gemini.example.com", key_env="GEMINI_KEY_2")

        settings = home / ".gemini" / "antigravity-cli" / "settings.json"
        assert json.loads(settings.read_text()) == {"modelProvider": "gemini"}
        assert env_vars(get_environment("relay"), "agy") == {
            "GEMINI_API_KEY": "${GEMINI_KEY_2}",
            "GOOGLE_GEMINI_BASE_URL": "https://gemini.example.com",
        }

        clear_endpoint("relay", "agy")
        assert json.loads(settings.read_text()) == {}

    def test_what_an_agent_cannot_take_is_refused(self, home: Path) -> None:
        with pytest.raises(ValueError, match="Custom tab of /model"):
            set_endpoint("relay", "qodercli", "https://x.example.com")
        with pytest.raises(ValueError, match="speaks anthropic"):
            set_endpoint("relay", "claude", "https://x.example.com", api="openai")
        with pytest.raises(ValueError, match="--model"):
            set_endpoint("relay", "copilot", "https://x.example.com")
        with pytest.raises(ValueError, match="not a URL"):
            set_endpoint("relay", "claude", "relay.example.com")
        with pytest.raises(ValueError, match="name of a variable"):
            set_endpoint("relay", "claude", "https://x.example.com", key_env="${X}")

    def test_endpoints_in_the_agents_own_files_are_read_too(self, home: Path) -> None:
        (home / ".claude").mkdir()
        (home / ".claude" / "settings.json").write_text(json.dumps(
            {"env": {"ANTHROPIC_BASE_URL": "https://glm.example.com/anthropic", "ANTHROPIC_AUTH_TOKEN": KEY}}
        ))
        (home / ".copilot").mkdir()
        (home / ".copilot" / "providers.json").write_text(json.dumps(
            {"providers": [{"name": "p", "baseUrl": "http://127.0.0.1:11434/v1"}], "models": []}
        ))

        claude = endpoint_of(home, "claude")
        copilot = endpoint_of(home, "copilot")

        assert (claude.url, claude.key) == ("https://glm.example.com/anthropic", "ANTHROPIC_AUTH_TOKEN in .claude/settings.json")
        assert KEY not in repr(claude)
        assert (copilot.url, copilot.key) == ("http://127.0.0.1:11434/v1", None)
        assert endpoint_of(home, "gemini") is None


class TestCodexApiKeyLogins:
    def test_an_imported_api_key_keeps_its_base_url(self, tmp_path: Path) -> None:
        from clippet.auth.codex import CodexCredential

        credential = CodexCredential(auth_mode="apikey", api_key=KEY, base_url="https://gw.example.com/v1")

        home, _ = import_codex_credential("gw", credential, source="test", seed_config=False)

        config = tomllib.loads((home / ".codex" / "config.toml").read_text())
        assert config["openai_base_url"] == "https://gw.example.com/v1"
        assert KEY not in (home / ".codex" / "config.toml").read_text()
        assert read_env_codex_credential(home).base_url == "https://gw.example.com/v1"
        assert endpoint_of(home, "codex").url == "https://gw.example.com/v1"

    def test_base_url_can_be_removed_again(self, home: Path) -> None:
        set_codex_base_url(home, "https://gw.example.com/v1")
        set_codex_base_url(home, None)

        assert "openai_base_url" not in (home / ".codex" / "config.toml").read_text()


class TestLoginsStayBehind:
    def test_seeding_reports_the_endpoint_keys_it_left_out(self, tmp_path: Path) -> None:
        real = Path.home()
        (real / ".claude").mkdir()
        (real / ".claude" / "settings.json").write_text(json.dumps({
            "model": "opus",
            "env": {"ANTHROPIC_BASE_URL": "https://relay.example.com", "ANTHROPIC_AUTH_TOKEN": KEY, "FOO": "1"},
        }))
        (real / ".gemini" / "antigravity-cli").mkdir(parents=True)
        (real / ".gemini" / "antigravity-cli" / "settings.json").write_text(json.dumps({"modelProvider": "gemini"}))

        home, report = create_home_env_with_report("seeded", from_current=True, agents=["claude", "agy"])

        assert json.loads((home / ".claude" / "settings.json").read_text()) == {"model": "opus", "env": {"FOO": "1"}}
        assert json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text()) == {}
        assert ".claude/settings.json: env.ANTHROPIC_AUTH_TOKEN" in report.skipped_credentials
        assert ".gemini/antigravity-cli/settings.json: modelProvider" in report.skipped_credentials

    def test_a_clone_without_auth_leaves_logins_folders_and_endpoints_behind(self, home: Path) -> None:
        (home / ".qoder" / ".auth").mkdir(parents=True)
        (home / ".qoder" / ".auth" / "user").write_text("secret")
        (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "alice@example.com"}, "x": 1}))
        set_env_vars("relay", "claude", {"ANTHROPIC_AUTH_TOKEN": "${RELAY_KEY}"})

        bare = clone_home_env("relay", "bare")
        full = clone_home_env("relay", "full", with_auth=True)

        assert not (bare / ".qoder" / ".auth").exists()
        assert json.loads((bare / ".claude.json").read_text()) == {"x": 1}
        assert "vars" not in get_environment("bare")
        assert (full / ".qoder" / ".auth" / "user").read_text() == "secret"
        assert env_vars(get_environment("full"), "claude") == {"ANTHROPIC_AUTH_TOKEN": "${RELAY_KEY}"}
