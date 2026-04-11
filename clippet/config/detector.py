"""Config file format detection and adapter creation for CLIppet.

Supports three input types for ``-c``:
1. A CLIppet composite JSON/YAML file (has ``adapters`` list).
2. A native agent config file (Claude Code JSON or Codex JSON).
3. A **directory** path — treated as a "second home" that is used directly
   as ``$HOME`` for the launched agent.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from clippet.adapters.base import BaseAdapter, BaseSubprocessAdapter
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.models import IsolationConfig

_CLAUDE_CODE_KEYS = {"effortLevel", "skipDangerousModePermissionPrompt", "permissions"}


# ---------------------------------------------------------------------------
# Config type detection
# ---------------------------------------------------------------------------


def detect_config_type(
    path: Path | str,
) -> Literal["claude_code", "codex", "clippet", "second_home"]:
    """Detect the format of a configuration file **or directory**.

    Args:
        path: Path to a JSON config file **or** a directory to use as second
            home.

    Returns:
        One of ``"clippet"``, ``"claude_code"``, ``"codex"``, or
        ``"second_home"``.

    Raises:
        ValueError: If the file format cannot be determined.
        FileNotFoundError: If *path* does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """

    path = Path(path)

    # --- Directory → second home -------------------------------------------
    if path.is_dir():
        return "second_home"

    with open(path, "r", encoding="utf-8") as f:
        data: dict = json.load(f)

    # CLIppet composite format: has an "adapters" list
    if isinstance(data.get("adapters"), list):
        return "clippet"

    # Claude Code format: env dict with ANTHROPIC_* keys, or known top-level keys
    env = data.get("env")
    if isinstance(env, dict):
        if any(key.startswith("ANTHROPIC_") for key in env):
            return "claude_code"

    if _CLAUDE_CODE_KEYS & data.keys():
        return "claude_code"

    # Codex format: OPENAI_API_KEY at top level
    if "OPENAI_API_KEY" in data:
        return "codex"

    raise ValueError(
        f"Unrecognizable config format in '{path}'. "
        "Expected a CLIppet composite config (with 'adapters' list), "
        "a Claude Code config (with 'env' containing ANTHROPIC_* keys), "
        "or a Codex config (with top-level 'OPENAI_API_KEY')."
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
    """Extract the ``model`` value from a Codex ``config.toml``.

    Uses a simple regex so we don't need a TOML parser dependency.
    """

    try:
        text = toml_path.read_text(encoding="utf-8")
        match = re.search(r'^model\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def create_adapter_from_codex_config(
    config_path: Path | str | None = None,
    config_toml_path: Path | str | None = None,
    **adapter_kwargs,
) -> CodexAdapter:
    """Create a :class:`CodexAdapter` from Codex credential/config files.

    Supports three usage patterns:

    * **auth.json only** (``config_path`` set): the ``config.toml`` is read
      from the real ``~/.codex/config.toml`` if it exists.
    * **config.toml only** (``config_toml_path`` set): the ``auth.json`` is
      read from the real ``~/.codex/auth.json``.
    * **Both files** (both set): both are copied into the sandbox.

    Args:
        config_path: Path to the Codex auth JSON file (typically
            ``auth.json``).  When *None*, falls back to
            ``~/.codex/auth.json``.
        config_toml_path: Path to the Codex ``config.toml``.  When *None*,
            falls back to ``~/.codex/config.toml``.
        **adapter_kwargs: Extra keyword arguments forwarded to
            :class:`CodexAdapter`.

    Returns:
        A configured :class:`CodexAdapter` instance.

    Raises:
        FileNotFoundError: If neither an explicit nor a fallback auth file
            can be located.
    """

    # --- Resolve auth.json --------------------------------------------------
    if config_path is not None:
        auth_path = Path(config_path).resolve()
    else:
        auth_path = Path.home() / ".codex" / "auth.json"
        if not auth_path.is_file():
            raise FileNotFoundError(
                "No auth.json provided and ~/.codex/auth.json does not exist."
            )

    # --- Resolve config.toml ------------------------------------------------
    if config_toml_path is not None:
        toml_path: Path | None = Path(config_toml_path).resolve()
    else:
        candidate = Path.home() / ".codex" / "config.toml"
        toml_path = candidate if candidate.is_file() else None

    # --- Read auth.json and extract env vars --------------------------------
    env_overrides: dict[str, str] = {}
    if auth_path.is_file():
        with open(auth_path, "r", encoding="utf-8") as f:
            data: dict = json.load(f)
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            if key in data:
                env_overrides[key] = data[key]

    credential_files: dict[str, str] = {
        ".codex/auth.json": str(auth_path),
    }

    if toml_path is not None and toml_path.is_file():
        credential_files[".codex/config.toml"] = str(toml_path)

        # Read model from config.toml so the CLI --model flag is consistent
        if "model" not in adapter_kwargs:
            model = _read_model_from_codex_toml(toml_path)
            if model:
                adapter_kwargs["model"] = model

    isolation = IsolationConfig(
        credential_files=credential_files,
        env_overrides=env_overrides,
    )

    adapter = CodexAdapter(**adapter_kwargs)
    adapter.default_isolation = isolation
    return adapter


# ---------------------------------------------------------------------------
# Adapter factory — second-home mode
# ---------------------------------------------------------------------------


def create_adapter_with_second_home(
    home_dir: Path | str,
    agent_type: str,
    **adapter_kwargs,
) -> BaseSubprocessAdapter:
    """Create an adapter that uses a persistent *second home* directory.

    The directory at ``home_dir`` is used **directly** as ``$HOME``.  It
    should already contain the appropriate agent config hierarchy (e.g.
    ``.claude/``, ``.codex/``).

    Args:
        home_dir: Path to the second-home directory.
        agent_type: ``"claude"`` / ``"claude_code"`` or ``"codex"``.
        **adapter_kwargs: Extra keyword arguments forwarded to the adapter
            constructor.

    Returns:
        A configured adapter instance.

    Raises:
        ValueError: If *agent_type* is not recognised.
    """

    home_dir = str(Path(home_dir).resolve())

    isolation = IsolationConfig(home_dir=home_dir)

    if agent_type in ("claude", "claude_code"):
        adapter = ClaudeAdapter(**adapter_kwargs)
    elif agent_type == "codex":
        adapter = CodexAdapter(**adapter_kwargs)
    else:
        raise ValueError(
            f"Second-home mode is not supported for agent type '{agent_type}'. "
            "Supported types: 'claude', 'codex'."
        )

    adapter.default_isolation = isolation
    return adapter


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------


def create_adapter_from_config_file(
    config_path: Path | str | None = None,
    agent_type: str | None = None,
    codex_config_path: Path | str | None = None,
) -> BaseSubprocessAdapter:
    """Create an adapter from a standalone config file **or** directory.

    This is a dispatcher that auto-detects (or uses the provided
    *agent_type*) the config format and delegates to the appropriate
    factory function.

    Args:
        config_path: Path to the JSON configuration file **or** a directory
            to use as second home.  May be *None* when *codex_config_path*
            is provided (auth.json will be read from ``~/.codex/``).
        agent_type: Explicit agent type override (``"claude_code"`` or
            ``"codex"``).  When *None*, the type is inferred from the
            file contents.
        codex_config_path: Optional path to a Codex ``config.toml`` file.
            When provided together with a Codex *config_path* (or on its
            own), both files are wired into the sandbox.

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
        )

    if config_path is None:
        raise ValueError(
            "Either -c/--config or --codex-config must be provided."
        )

    detected = detect_config_type(config_path)

    # --- Second-home (directory) -------------------------------------------
    if detected == "second_home":
        if agent_type is None:
            raise ValueError(
                "agent_type is required when -c points to a directory "
                "(second-home mode).  Specify one of: claude, codex."
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

    if effective_type == "claude_code":
        return create_adapter_from_claude_config(config_path)

    if effective_type == "codex":
        return create_adapter_from_codex_config(
            config_path=config_path,
            config_toml_path=codex_config_path,
        )

    raise ValueError(
        f"Unknown agent type: '{effective_type}'. "
        "Supported types are: 'claude_code', 'codex'."
    )
