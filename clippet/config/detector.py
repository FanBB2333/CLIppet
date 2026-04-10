"""Config file format detection and adapter creation for CLIppet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.models import IsolationConfig

_CLAUDE_CODE_KEYS = {"effortLevel", "skipDangerousModePermissionPrompt", "permissions"}


def detect_config_type(path: Path | str) -> Literal["claude_code", "codex", "clippet"]:
    """Detect the format of a JSON configuration file.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        One of ``"clippet"``, ``"claude_code"``, or ``"codex"``.

    Raises:
        ValueError: If the file format cannot be determined.
        FileNotFoundError: If *path* does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """

    path = Path(path)

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


def create_adapter_from_codex_config(
    config_path: Path | str,
    **adapter_kwargs,
) -> CodexAdapter:
    """Create a :class:`CodexAdapter` from a Codex JSON config file.

    Args:
        config_path: Path to the Codex JSON config file.
        **adapter_kwargs: Extra keyword arguments forwarded to
            :class:`CodexAdapter`.

    Returns:
        A configured :class:`CodexAdapter` instance.
    """

    config_path = Path(config_path).resolve()

    with open(config_path, "r", encoding="utf-8") as f:
        data: dict = json.load(f)

    env_overrides: dict[str, str] = {}
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        if key in data:
            env_overrides[key] = data[key]

    isolation = IsolationConfig(
        credential_files={
            ".codex/auth.json": str(config_path),
        },
        env_overrides=env_overrides,
    )

    adapter = CodexAdapter(**adapter_kwargs)
    adapter.default_isolation = isolation
    return adapter


def create_adapter_from_config_file(
    config_path: Path | str,
    agent_type: str | None = None,
) -> BaseSubprocessAdapter:
    """Create an adapter from a standalone config file.

    This is a dispatcher that auto-detects (or uses the provided
    *agent_type*) the config format and delegates to the appropriate
    factory function.

    Args:
        config_path: Path to the JSON configuration file.
        agent_type: Explicit agent type override (``"claude_code"`` or
            ``"codex"``).  When *None*, the type is inferred from the
            file contents.

    Returns:
        A configured adapter instance.

    Raises:
        ValueError: If the config is a CLIppet composite format (use
            :func:`load_config` / :func:`create_runner_from_config`
            instead), or if the format is unrecognizable.
    """

    detected = detect_config_type(config_path)

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
        return create_adapter_from_codex_config(config_path)

    raise ValueError(
        f"Unknown agent type: '{effective_type}'. "
        "Supported types are: 'claude_code', 'codex'."
    )
