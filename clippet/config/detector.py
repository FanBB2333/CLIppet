"""Config file format detection and adapter creation for CLIppet.

Supports these input types for ``-c``:
1. A CLIppet composite JSON/YAML file (has ``adapters`` list).
2. A Claude Code settings JSON file (with an ``env`` block).
3. A Codex credential in any format understood by :mod:`clippet.auth.codex`
   (Codex ``auth.json``, Cockpit Tools export, sub2api export, CLIProxyAPI
   token file, plain token bundle, JSON arrays / JSON Lines of those).
4. A **directory** path — treated as a "second home": the env the agent runs
   in (its config directory, e.g. ``CODEX_HOME=<dir>/.codex``, or the whole
   ``$HOME``, depending on the isolation scope).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.agents import agent_list, agent_spec
from clippet.auth.codex import (
    AUTH_MODE_APIKEY,
    FORMAT_NATIVE,
    CodexCredential,
    _parse_text,
    extract_codex_credentials,
    load_codex_credentials,
    select_credential,
)
from clippet.host import host_home
from clippet.isolation import SCOPE_AGENT, effective_scope
from clippet.models import IsolationConfig

#: ``-c`` override that makes Codex read the auth.json CLIppet injected even
#: when the user's config.toml selects the OS keyring.
CODEX_FILE_STORE_OVERRIDE = ("cli_auth_credentials_store", '"file"')

_CLAUDE_CODE_KEYS = {"effortLevel", "skipDangerousModePermissionPrompt", "permissions"}


# ---------------------------------------------------------------------------
# Config type detection
# ---------------------------------------------------------------------------


def detect_config_type(
    path: Path | str,
) -> Literal["claude_code", "codex", "clippet", "second_home"]:
    """Detect the format of a configuration file **or directory**.

    Args:
        path: Path to a JSON config or credential file, **or** a directory to
            use as second home.

    Returns:
        One of ``"clippet"``, ``"claude_code"``, ``"codex"`` or
        ``"second_home"``.

    Raises:
        ValueError: If the file format cannot be determined.
        FileNotFoundError: If *path* does not exist.
        json.JSONDecodeError: If the JSON file is not valid JSON.
    """

    path = Path(path)

    # --- Directory → Cockpit Tools data dir, or second home ----------------
    if path.is_dir():
        from clippet.auth.cockpit import is_cockpit_dir

        if is_cockpit_dir(path):
            return "codex"
        return "second_home"

    # --- .env files were Gemini CLI's, which CLIppet no longer runs ---------
    if path.suffix == ".env" or path.name == ".env":
        raise ValueError(
            f"'{path}' is a .env file, which CLIppet does not read "
            "(Gemini CLI is no longer supported)."
        )

    # --- Try to parse as JSON ----------------------------------------------
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        # Not one JSON value: maybe a JSON Lines file of Codex credentials
        content = path.read_text(encoding="utf-8")
        try:
            if extract_codex_credentials(_parse_text(content)):
                return "codex"
        except ValueError:
            pass
        raise

    if not isinstance(data, dict):
        if extract_codex_credentials(data):
            return "codex"
        raise ValueError(
            f"Unrecognizable config format in '{path}': top-level JSON value "
            "is not an object and holds no Codex credentials."
        )

    # CLIppet composite format: has an "adapters" list
    if isinstance(data.get("adapters"), list):
        return "clippet"

    # Claude Code format: env dict with ANTHROPIC_* keys, or known top-level keys
    env = data.get("env")
    if isinstance(env, dict) and any(key.startswith("ANTHROPIC_") for key in env):
        return "claude_code"

    if _CLAUDE_CODE_KEYS & data.keys():
        return "claude_code"

    # Codex: native auth.json (API key or ChatGPT tokens) or any account
    # manager export that carries Codex OAuth tokens.
    if "OPENAI_API_KEY" in data or extract_codex_credentials(data):
        return "codex"

    raise ValueError(
        f"Unrecognizable config format in '{path}'. "
        "Expected a CLIppet composite config (with 'adapters' list), "
        "a Claude Code config (with 'env' containing ANTHROPIC_* keys), "
        "or a Codex credential (auth.json, Cockpit Tools / sub2api / CLIProxyAPI "
        "export, or a token bundle)."
    )


# ---------------------------------------------------------------------------
# Adapter factories — single-file mode
# ---------------------------------------------------------------------------


def create_adapter_from_claude_config(
    config_path: Path | str,
    **adapter_kwargs,
) -> ClaudeAdapter:
    """Create a :class:`ClaudeAdapter` from a Claude Code JSON config file.

    The function reads environment variables from the ``"env"`` dict in the
    config and wires them into an :class:`IsolationConfig` so they are
    available inside the sandbox at runtime.

    Args:
        config_path: Path to the Claude Code JSON config file.
        **adapter_kwargs: Extra keyword arguments forwarded to
            :class:`ClaudeAdapter`.

    Returns:
        A configured :class:`ClaudeAdapter` instance.
    """

    config_path = Path(config_path).resolve()

    with open(config_path, "r", encoding="utf-8") as f:
        data: dict = json.load(f)

    env_vars: dict[str, str] = data.get("env", {})

    isolation = IsolationConfig(
        credential_files={
            ".claude/settings.json": str(config_path),
        },
        env_overrides=dict(env_vars),
    )

    # Infer model from env vars when not explicitly provided
    if "model" not in adapter_kwargs:
        model = env_vars.get("ANTHROPIC_MODEL") or env_vars.get(
            "ANTHROPIC_DEFAULT_SONNET_MODEL"
        )
        if model:
            adapter_kwargs["model"] = model

    adapter = ClaudeAdapter(**adapter_kwargs)
    adapter.default_isolation = isolation
    return adapter


def _read_model_from_codex_toml(toml_path: Path) -> str | None:
    """Extract the top-level ``model`` value from a Codex ``config.toml``.

    Keys inside tables (``[profiles.x]``, ``[model_providers.y]``) are ignored.
    """

    try:
        text = toml_path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        tomllib = None

    if tomllib is not None:
        try:
            model = tomllib.loads(text).get("model")
        except tomllib.TOMLDecodeError:
            model = None
        return model if isinstance(model, str) and model else None

    for line in text.splitlines():  # pragma: no cover - Python 3.10 fallback
        stripped = line.strip()
        if stripped.startswith("["):
            break
        match = re.match(r'^model\s*=\s*["\']([^"\']+)["\']', stripped)
        if match:
            return match.group(1)
    return None


def _refreshed_credential_path(credential: CodexCredential) -> Path:
    """Where refreshed tokens of a converted (non-native) source are kept."""

    from clippet.config.environments import get_clippet_root

    who = credential.display_email or credential.display_account_id or "account"
    slug = re.sub(r"[^A-Za-z0-9._@-]+", "_", who)
    return get_clippet_root() / "refreshed" / f"{slug}.auth.json"


def codex_isolation_for_credential(
    credential: CodexCredential,
    *,
    source_is_single_native_file: bool,
    config_toml: Path | None = None,
) -> IsolationConfig:
    """Build the isolation config that injects *credential* as auth.json."""

    credential_files: dict[str, str] = {}
    credential_contents: dict[str, SecretStr] = {}
    sync_back: dict[str, str] = {}

    if source_is_single_native_file and credential.source_path:
        credential_files[".codex/auth.json"] = credential.source_path
        sync_back[".codex/auth.json"] = credential.source_path
    else:
        credential_contents[".codex/auth.json"] = SecretStr(credential.to_auth_json_text())
        if credential.auth_mode != AUTH_MODE_APIKEY:
            sync_back[".codex/auth.json"] = str(_refreshed_credential_path(credential))

    if config_toml is not None and config_toml.is_file():
        credential_files[".codex/config.toml"] = str(config_toml)

    env_overrides: dict[str, str] = {}
    if credential.auth_mode == AUTH_MODE_APIKEY and credential.api_key:
        # Custom providers may read the key via ``env_key = "OPENAI_API_KEY"``.
        env_overrides["OPENAI_API_KEY"] = credential.api_key

    return IsolationConfig(
        credential_files=credential_files,
        credential_contents=credential_contents,
        sync_back=sync_back,
        env_overrides=env_overrides,
    )


def default_codex_home() -> Path:
    """Where Codex keeps its state for a run started from here: ``$CODEX_HOME``, else ``~/.codex``.

    Inside an env whose Codex keeps your HOME, ``$CODEX_HOME`` is the env's,
    so the "current" login is the env's one, as for Codex itself.
    """

    override = os.environ.get("CODEX_HOME")
    return Path(override).expanduser() if override else Path.home() / ".codex"


def create_adapter_from_codex_config(
    config_path: Path | str | None = None,
    config_toml_path: Path | str | None = None,
    account: str | int | None = None,
    **adapter_kwargs,
) -> CodexAdapter:
    """Create a :class:`CodexAdapter` from Codex credential/config files.

    Supports three usage patterns:

    * **credential only** (``config_path`` set): the ``config.toml`` is read
      from Codex's own ``config.toml`` (``$CODEX_HOME``, else ``~/.codex``) if
      it exists.
    * **config.toml only** (``config_toml_path`` set): the credential is read
      from Codex's own ``auth.json`` there.
    * **Both files** (both set): both are injected into the sandbox.

    The credential may be in any format understood by
    :func:`clippet.auth.codex.load_codex_credentials` (Codex ``auth.json``,
    Cockpit Tools export or live store, sub2api, CLIProxyAPI, token bundles).
    Non-native formats are converted into Codex's ``auth.json``.

    Args:
        config_path: Credential source.  When *None*, falls back to
            Codex's own ``auth.json`` (see :func:`default_codex_home`).
        config_toml_path: Path to the Codex ``config.toml``.  When *None*,
            falls back to Codex's own ``config.toml``.
        account: Selector for sources holding several accounts (1-based
            index, e-mail, e-mail local part, label or account id).
        **adapter_kwargs: Extra keyword arguments forwarded to
            :class:`CodexAdapter`.

    Raises:
        FileNotFoundError: If neither an explicit nor a fallback credential
            can be located.
        ValueError: If the source holds no usable credential, or the account
            selection is missing/ambiguous.
    """

    # --- Resolve the credential source ------------------------------------
    if config_path is not None:
        source = str(config_path)
        if not source.startswith("cockpit"):
            source = str(Path(source).expanduser().resolve())
    else:
        default_auth = default_codex_home() / "auth.json"
        if not default_auth.is_file():
            raise FileNotFoundError(
                f"No auth.json provided and {default_auth} does not exist."
            )
        source = str(default_auth)

    credentials = load_codex_credentials(source)
    credential = select_credential(credentials, account)

    # --- Resolve config.toml ------------------------------------------------
    if config_toml_path is not None:
        toml_path: Path | None = Path(config_toml_path).expanduser().resolve()
    else:
        candidate = default_codex_home() / "config.toml"
        toml_path = candidate if candidate.is_file() else None

    single_native = (
        len(credentials) == 1
        and credential.source_format == FORMAT_NATIVE
        and credential.source_path is not None
    )
    isolation = codex_isolation_for_credential(
        credential,
        source_is_single_native_file=single_native,
        config_toml=toml_path,
    )

    if toml_path is not None and toml_path.is_file() and "model" not in adapter_kwargs:
        model = _read_model_from_codex_toml(toml_path)
        if model:
            adapter_kwargs["model"] = model

    overrides = dict(adapter_kwargs.pop("config_overrides", None) or {})
    key, value = CODEX_FILE_STORE_OVERRIDE
    overrides.setdefault(key, value)
    if credential.auth_mode == AUTH_MODE_APIKEY and credential.base_url:
        # Codex reads the built-in provider's endpoint from ``openai_base_url``
        # (the OPENAI_BASE_URL environment variable is no longer consulted).
        overrides.setdefault("openai_base_url", json.dumps(credential.base_url))

    adapter = CodexAdapter(config_overrides=overrides, **adapter_kwargs)
    adapter.default_isolation = isolation
    return adapter


# ---------------------------------------------------------------------------
# Adapter factory — second-home mode
# ---------------------------------------------------------------------------


def create_adapter_with_second_home(
    home_dir: Path | str,
    agent_type: str,
    isolation_scope: str | None = None,
    **adapter_kwargs,
) -> BaseSubprocessAdapter:
    """Create an adapter that uses a persistent *second home* directory.

    The directory at ``home_dir`` is used **directly** as the env: the agent's
    config directory inside it (``CODEX_HOME=<dir>/.codex``, isolation scope
    ``agent``) or the whole ``$HOME`` (scope ``home``).  It should already
    contain the appropriate agent config hierarchy (e.g. ``.claude/``,
    ``.codex/``, ``.qoder/``).

    Args:
        home_dir: Path to the second-home directory.
        agent_type: a key of :data:`clippet.agents.AGENTS` (``"claude_code"``
            is taken for ``"claude"``).
        isolation_scope: ``"agent"`` or ``"home"``; by default the scope the
            registered env living there asks for, else CLIppet's default.
        **adapter_kwargs: Extra keyword arguments forwarded to the adapter
            constructor.

    Returns:
        A configured adapter instance.

    Raises:
        ValueError: If *agent_type* is not recognised.
    """

    home_dir = str(Path(home_dir).resolve())

    spec = agent_spec(agent_type)
    if spec is None:
        raise ValueError(
            f"Second-home mode is not supported for agent type '{agent_type}'. "
            f"Supported types: {agent_list()}."
        )
    run_env: dict[str, str] = {}
    is_env = Path(home_dir) != host_home().resolve()
    if is_env:
        from clippet.config.environments import isolation_scope_for_home
        from clippet.endpoints import run_vars

        # an env is not your real HOME: what the agent should do differently
        # there, then what the env sets for it (a third-party endpoint, ...)
        run_env.update(spec.run_env)
        run_env.update(run_vars(home_dir, spec.key))
        if isolation_scope is None:
            isolation_scope = isolation_scope_for_home(home_dir)
    scope_field = {"isolation_scope": isolation_scope} if isolation_scope is not None else {}
    isolation = IsolationConfig(home_dir=home_dir, env_overrides=run_env, **scope_field)
    keeps_home = effective_scope(isolation_scope, spec.key) == SCOPE_AGENT
    if spec.key == "codex" and ((Path(home_dir) / ".codex" / "auth.json").is_file() or (is_env and keeps_home)):
        # The env carries its login as a file; make sure a copied
        # config.toml selecting the OS keyring cannot hide it.  With your
        # HOME the keychain is in reach, so a login made in the env must be
        # told to land in its auth.json too.
        overrides = dict(adapter_kwargs.pop("config_overrides", None) or {})
        key, value = CODEX_FILE_STORE_OVERRIDE
        overrides.setdefault(key, value)
        adapter_kwargs["config_overrides"] = overrides
    adapter = spec.adapter_class()(**adapter_kwargs)

    adapter.default_isolation = isolation
    return adapter


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------


def create_adapter_from_config_file(
    config_path: Path | str | None = None,
    agent_type: str | None = None,
    codex_config_path: Path | str | None = None,
    account: str | int | None = None,
) -> BaseSubprocessAdapter:
    """Create an adapter from a standalone config file **or** directory.

    This is a dispatcher that auto-detects (or uses the provided
    *agent_type*) the config format and delegates to the appropriate
    factory function.

    Args:
        config_path: Path to the JSON configuration or credential file, **or**
            a directory to use as second home.  May be *None* when
            *codex_config_path* is provided (auth.json will be read from
            ``~/.codex/``).
        agent_type: Explicit agent type override (``"claude_code"`` or
            ``"codex"``).  When *None*, the type is inferred from the file
            contents.
        codex_config_path: Optional path to a Codex ``config.toml`` file.
            When provided together with a Codex *config_path* (or on its
            own), both files are wired into the sandbox.
        account: Account selector for Codex sources that hold several
            accounts (Cockpit / sub2api exports, ...).

    Returns:
        A configured adapter instance.

    Raises:
        ValueError: If the config is a CLIppet composite format (use
            :func:`load_config` / :func:`create_runner_from_config`
            instead), or if the format is unrecognisable.
    """

    # --- codex_config_path without config_path → codex-only shortcut -------
    if config_path is None and codex_config_path is not None:
        return create_adapter_from_codex_config(
            config_path=None,
            config_toml_path=codex_config_path,
            account=account,
        )

    if config_path is None:
        raise ValueError(
            "Either -c/--config or --codex-config must be provided."
        )

    if str(config_path) == "cockpit" or str(config_path).startswith("cockpit:"):
        if agent_type not in (None, "codex"):
            raise ValueError("Cockpit Tools sources only provide Codex accounts.")
        return create_adapter_from_codex_config(
            config_path=str(config_path),
            config_toml_path=codex_config_path,
            account=account,
        )

    detected = detect_config_type(config_path)

    # --- Second-home (directory) -------------------------------------------
    if detected == "second_home":
        if agent_type is None:
            raise ValueError(
                "agent_type is required when -c points to a directory "
                f"(second-home mode).  Specify one of: {agent_list()}."
            )
        effective_type = "claude_code" if agent_type == "claude" else agent_type
        return create_adapter_with_second_home(
            config_path, effective_type, **{}
        )

    if detected == "clippet":
        raise ValueError(
            "The config file is in CLIppet composite format. "
            "Use load_config() / create_runner_from_config() instead."
        )

    # Use explicit agent_type when provided, otherwise use detected type
    effective_type = agent_type if agent_type is not None else detected
    if effective_type == "claude":
        effective_type = "claude_code"

    if effective_type == "claude_code":
        return create_adapter_from_claude_config(config_path)

    if effective_type == "codex":
        return create_adapter_from_codex_config(
            config_path=config_path,
            config_toml_path=codex_config_path,
            account=account,
        )

    raise ValueError(
        f"Unknown agent type: '{effective_type}'. "
        "Supported types are: 'claude_code', 'codex'."
    )
