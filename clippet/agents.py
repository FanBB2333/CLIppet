"""The agent CLIs CLIppet can run, and what differs between them.

For each agent: the binary and how to install it, the files it keeps under
HOME (and which of them are a login), the variables that would point it at
another identity when inherited, how to pick one of its conversations back
up, and how the TUI draws it.  The rest of CLIppet reads these tables instead
of naming agents one by one, so supporting another agent starts here.

This module imports nothing from the rest of CLIppet, so anything may import it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SeedSpec:
    """What ``--from-current`` copies for one agent (paths relative to HOME).

    *entries* are configuration (settings, instructions, skills, MCP servers);
    *credentials* are logins (files or folders), copied only with
    ``--with-auth``; *exclude* are names left out when an entry is a directory.
    *scrub* names JSON files among the entries whose login fields are removed
    unless ``--with-auth``: dotted key paths, the last part may end in ``*``
    (``"env.ANTHROPIC_*"``).
    """

    entries: tuple[str, ...]
    credentials: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    scrub: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSpec:
    """One agent CLI."""

    key: str  # what CLIppet calls it: ``clippet <key>``, ``.clippet.json`` "agents"
    title: str  # what its maker calls it
    binary: str
    install: str  # how to install it, as a command or a pointer
    docs: str  # where its documentation lives
    adapter: str  # ``module:Class`` of its adapter, imported when first needed
    config_paths: tuple[str, ...]  # what it keeps under HOME; any of them means "configured here"
    seed: SeedSpec | None = None  # None: --from-current has nothing to copy for it
    identity_env: tuple[str, ...] = ()  # inherited variables dropped in env runs (``NAME`` or ``PREFIX*``)
    resume: tuple[str, ...] = ()  # arguments that reopen a conversation; ``{id}`` is its session id
    login: str = ""  # how to log in from inside the agent, said to people
    keychain: str = ""  # when its login can live outside HOME (an OS keychain), what to tell people
    runtime: tuple[str, ...] = ()  # what its own installation needs from the real HOME, linked into envs
    run_env: dict[str, str] = field(default_factory=dict)  # set for runs in an env (not in your real HOME)
    letter: str = "?"  # its mark in the TUI's agent column
    color: str = "ink"  # its colour in the TUI (a palette key)
    examples: str = ""  # arguments worth showing as an example

    def resume_args(self, session_id: str | None) -> list[str]:
        """The arguments that reopen conversation *session_id* ([] when it cannot be reopened)."""

        if not session_id or not self.resume:
            return []
        return [part.replace("{id}", session_id) for part in self.resume]

    def adapter_class(self) -> Any:
        module, _, name = self.adapter.partition(":")
        return getattr(importlib.import_module(module), name)


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        key="codex",
        title="Codex CLI",
        binary="codex",
        install="npm i -g @openai/codex",
        docs="https://developers.openai.com/codex/cli",
        adapter="clippet.adapters.codex:CodexAdapter",
        config_paths=(".codex",),
        seed=SeedSpec(
            entries=(
                ".codex/config.toml",
                ".codex/AGENTS.md",
                ".codex/AGENTS.override.md",
                ".codex/hooks.json",
                ".codex/keybindings.json",
                ".codex/skills",
                ".codex/prompts",
                ".codex/rules",
            ),
            credentials=(".codex/auth.json",),
        ),
        identity_env=(
            "CODEX_HOME",
            "CODEX_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "CODEX_REFRESH_TOKEN_URL_OVERRIDE",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG_ID",
            "OPENAI_ORGANIZATION",
            "OPENAI_PROJECT",
            "OPENAI_PROJECT_ID",
        ),
        resume=("resume", "{id}"),
        login="codex login",
        letter="X",
        color="ok",
        examples="e.g. resume --last   or   -m gpt-5.5",
    ),
    AgentSpec(
        key="claude",
        title="Claude Code",
        binary="claude",
        install="npm i -g @anthropic-ai/claude-code",
        docs="https://docs.claude.com/en/docs/claude-code",
        adapter="clippet.adapters.claude:ClaudeAdapter",
        config_paths=(".claude", ".claude.json"),
        seed=SeedSpec(
            entries=(
                ".claude/settings.json",
                ".claude/CLAUDE.md",
                ".claude/rules",
                ".claude/agents",
                ".claude/commands",
                ".claude/skills",
                ".claude/output-styles",
                ".claude/workflows",
                ".claude/agent-memory",
                ".claude/plugins",
                ".claude/themes",
                ".claude/keybindings.json",
                ".claude/hooks",
                ".claude.json",
            ),
            credentials=(".claude/.credentials.json",),
            # skills/synced and plugins/synced come from the claude.ai account.
            exclude=("synced",),
            scrub={
                ".claude.json": ("primaryApiKey", "oauthAccount", "userID", "customApiKeyResponses"),
                # user settings can carry a key helper or credentials in "env"
                ".claude/settings.json": (
                    "apiKeyHelper",
                    "awsAuthRefresh",
                    "awsCredentialExport",
                    "env.ANTHROPIC_*",
                    "env.CLAUDE_CODE_OAUTH_*",
                    "env.CLAUDE_CONFIG_DIR",
                    "env.CLAUDE_CODE_USE_*",
                    "env.CLAUDE_CODE_CLIENT_*",
                    "env.AWS_*",
                ),
            },
        ),
        identity_env=(
            "CLAUDE_CONFIG_DIR",
            "CLAUDE_SECURESTORAGE_CONFIG_DIR",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
            "CLAUDE_CODE_OAUTH_SCOPES",
            "ANTHROPIC_*",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_USE_ANTHROPIC_AWS",
            "CLAUDE_CODE_USE_MANTLE",
            "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
            "CLAUDE_CODE_SKIP_VERTEX_AUTH",
            "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
            "CLAUDE_CODE_SKIP_MANTLE_AUTH",
            "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH",
            "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
            "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
            "CLAUDE_CODE_CLIENT_CERT",
            "CLAUDE_CODE_CLIENT_KEY",
            "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE",
            "AWS_BEARER_TOKEN_BEDROCK",
            # set by the desktop app or by a Claude Code session CLIppet runs in
            "CLAUDE_CODE_USER_EMAIL",
            "CLAUDE_CODE_ACCOUNT_UUID",
            "CLAUDE_CODE_ORGANIZATION_UUID",
            "CLAUDE_CODE_ENTRYPOINT",
            "CLAUDECODE",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_CODE_SESSION_ID",
        ),
        resume=("--resume", "{id}"),
        login="/login",
        keychain=(
            "On macOS Claude Code keeps its login in the login Keychain, which a HOME does not separate. "
            "In an env it may keep it in .claude/.credentials.json there instead; if macOS says a keychain "
            "cannot be found, choose Cancel, never Reset To Defaults."
        ),
        # Its native installer updates itself under ~/.local; in an env that
        # would download a copy of Claude Code into the env that nothing runs.
        run_env={"DISABLE_AUTOUPDATER": "1"},
        letter="C",
        color="plum",
        examples="e.g. --continue   or   --model sonnet",
    ),
    AgentSpec(
        key="gemini",
        title="Gemini CLI",
        binary="gemini",
        install="npm i -g @google/gemini-cli",
        docs="https://github.com/google-gemini/gemini-cli",
        adapter="clippet.adapters.gemini:GeminiAdapter",
        config_paths=(".gemini",),
        seed=SeedSpec(
            entries=(
                ".gemini/settings.json",
                ".gemini/GEMINI.md",
                ".gemini/commands",
                ".gemini/extensions",
            ),
            credentials=(
                ".gemini/.env",
                ".gemini/oauth_creds.json",
                ".gemini/google_accounts.json",
            ),
        ),
        identity_env=(
            "GEMINI_CLI_HOME",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "GOOGLE_API_KEY",
            "GOOGLE_GEMINI_BASE_URL",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_GENAI_USE_VERTEXAI",
        ),
        login="/auth",
        letter="G",
        color="brand",
        examples="e.g. -m gemini-2.5-pro",
    ),
    AgentSpec(
        key="qodercli",
        title="Qoder CLI",
        binary="qodercli",
        install="curl -fsSL https://qoder.com/install | bash  (or: npm i -g @qoder-ai/qodercli)",
        docs="https://docs.qoder.com/cli/overview",
        adapter="clippet.adapters.qodercli:QoderCLIAdapter",
        config_paths=(".qoder", ".qoder-cli"),
        # ~/.qoder holds settings and instructions next to the login (.auth/,
        # an encrypted store keyed by .keychain-salt), BYOK models with their
        # keys (.models/), sessions (projects/) and caches, so only the
        # documented configuration is copied.
        seed=SeedSpec(
            entries=(
                ".qoder/settings.json",
                ".qoder/AGENTS.md",
                ".qoder/rules",
                ".qoder/commands",
                ".qoder/agents",
                ".qoder/skills",
                ".qoder/output-styles",
                ".qoder/workflows",
                ".qoder/hooks",
                ".qoder/keybindings.json",
                ".qoder/memory",
                ".agents/skills",
            ),
            credentials=(
                ".qoder/.auth",
                ".qoder/.keychain-salt",
                ".qoder/.models",
                ".qoder/.env",
                ".qoder/mcp-oauth-tokens.json",
                ".qoder/qoder-cli-credentials.json",
            ),
            # BYOK models can also be written into settings.json, keys included
            scrub={".qoder/settings.json": ("modelConfigs.customModels",)},
        ),
        identity_env=(
            "QODER_PERSONAL_ACCESS_TOKEN",
            "QODER_PAT",
            "QODER_ACCESS_TOKEN",
            "QODER_SDK_ACCESS_TOKEN",
            "QODER_SERVICE_ACCOUNT_KEY",
            "QODER_JOB_TOKEN",
            "QODER_DEVICE_TOKEN",
            "QODER_SDK_AUTH_PAYLOAD_FILE",
            "QODER_SDK_AUTH_CONFIG_DIR",
            "QODER_CONFIG_DIR",
            "QODER_CONFIG_DIR_NAME",
            "QODER_CLI_HOME",
            "QODER_HOME",
            "QODER_SERVER_ENDPOINT",
            "QODER_CENTER_ENDPOINT",
            "QODER_VPC_ENDPOINT",
            "QODER_ENV",
            "QODER_REMOTE_CONTROL_API_BASE",
            "QODER_MCP_CONFIG",
            "QODER_MODEL",
            "QODER_PERMISSION_MODE",
            "QODER_SESSION_ID",
            "QODER_WORKING_DIR",
            "QODER_APPEND_SYSTEM_PROMPT",
        ),
        resume=("-r", "{id}"),
        login="/login",
        letter="Q",
        color="info",
        examples="e.g. -c   or   --model performance",
    ),
)

_PI_PROVIDER_ENV = (
    # pi takes a provider's key from the environment when auth.json has no
    # login for it, and picks whichever provider has credentials when no
    # model is set (Amazon Bedrock first), so inherited keys could quietly
    # run an env as the parent shell's account.  From pi 1.5.0's env-api-keys.ts.
    "ANTHROPIC_*",
    "OPENAI_*",
    "AZURE_OPENAI_*",
    "COPILOT_GITHUB_TOKEN",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
    "AI_GATEWAY_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "NVIDIA_API_KEY",
    "FIREWORKS_API_KEY",
    "TOGETHER_API_KEY",
    "BASETEN_API_KEY",
    "KIMI_API_KEY",
    "KIMI_CODE_OAUTH_HOST",
    "KIMI_OAUTH_HOST",
    "META_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "MOONSHOT_API_KEY",
    "ANT_LING_API_KEY",
    "OPENCODE_API_KEY",
    "RADIUS_API_KEY",
    "TYPESAFE_API_KEY",
    "ZAI_API_KEY",
    "ZAI_CODING_CN_API_KEY",
    "QWEN_TOKEN_PLAN_API_KEY",
    "QWEN_TOKEN_PLAN_CN_API_KEY",
    "XIAOMI_*",
    "HF_TOKEN",
    "HF_TOKEN_PATH",
    "AWS_PROFILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_CONTAINER_CREDENTIALS_*",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "GOOGLE_CLOUD_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GCLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDFLARE_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_GATEWAY_ID",
)

_SPECS += (
    AgentSpec(
        key="pi",
        title="pi",
        binary="pi",
        install="npm i -g --ignore-scripts @earendil-works/pi-coding-agent  (or: curl -fsSL https://pi.dev/install.sh | sh)",
        docs="https://pi.dev/docs/latest",
        adapter="clippet.adapters.pi:PiAdapter",
        config_paths=(".pi",),
        # Everything lives in ~/.pi/agent.  models.json may hold API keys, so
        # it goes with the logins; sessions, caches, npm/git packages and the
        # managed install (install/, bin/) stay behind.
        seed=SeedSpec(
            entries=(
                ".pi/agent/settings.json",
                ".pi/agent/keybindings.json",
                ".pi/agent/mcp.json",
                ".pi/agent/AGENTS.md",
                ".pi/agent/AGENTS.override.md",
                ".pi/agent/CLAUDE.md",
                ".pi/agent/SYSTEM.md",
                ".pi/agent/APPEND_SYSTEM.md",
                ".pi/agent/trust.json",
                ".pi/agent/extensions",
                ".pi/agent/skills",
                ".pi/agent/prompts",
                ".pi/agent/themes",
                ".agents/skills",
            ),
            credentials=(".pi/agent/auth.json", ".pi/agent/mcp-auth.json", ".pi/agent/models.json"),
            # deviceId is sent to model providers; the rest is this machine's state.
            scrub={".pi/agent/settings.json": ("deviceId", "trackingId", "lastChangelogVersion",
                                               "extensionChangelogVersions")},
        ),
        identity_env=("PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR", "PI_PACKAGE_DIR", *_PI_PROVIDER_ENV),
        resume=("--session", "{id}"),
        login="/login",
        # The launcher of pi's own installer looks for the Node.js it installed
        # under $HOME/.local/share/pi-node.
        runtime=(".local/share/pi-node",),
        letter="π",
        color="rose",
        examples="e.g. --model sonnet:high   or   -c",
    ),
)

_SPECS += (
    AgentSpec(
        key="copilot",
        title="GitHub Copilot CLI",
        binary="copilot",
        install="npm i -g @github/copilot  (or: brew install --cask copilot-cli)",
        docs="https://docs.github.com/en/copilot/concepts/copilot-surfaces/copilot-cli",
        adapter="clippet.adapters.copilot:CopilotAdapter",
        config_paths=(".copilot",),
        # config.json is the CLI's own state: who is logged in, and the token
        # itself when no keychain is available; providers.json holds BYOK keys.
        seed=SeedSpec(
            entries=(
                ".copilot/settings.json",
                ".copilot/copilot-instructions.md",
                ".copilot/instructions",
                ".copilot/agents",
                ".copilot/skills",
                ".copilot/hooks",
                ".copilot/extensions",
                ".copilot/mcp-config.json",
                ".copilot/lsp-config.json",
                ".copilot/permissions-config.json",
                ".agents/skills",
            ),
            credentials=(
                ".copilot/config.json",
                ".copilot/providers.json",
                ".copilot/mcp-oauth-config",
                ".copilot/mcp-secrets",
            ),
        ),
        identity_env=(
            "COPILOT_HOME",
            "COPILOT_GITHUB_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GITHUB_ASKPASS",
            "GH_CONFIG_DIR",
            "GH_HOST",
            "COPILOT_GH_HOST",
            "COPILOT_API_URL",
            "GITHUB_COPILOT_INTEGRATION_ID",
            "GITHUB_COPILOT_API_TOKEN",
            "COPILOT_OFFLINE",
            "COPILOT_PROVIDER_*",
            "COPILOT_PROVIDERS_CONFIG",
            "COPILOT_ENABLE_ALT_PROVIDERS",
            "CAPI_HMAC_KEY",
            "COPILOT_HMAC_KEY",
            "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
            "COPILOT_SKILLS_DIRS",
        ),
        resume=("--resume={id}",),
        login="/login  (or: copilot login)",
        keychain=(
            "Copilot CLI keeps its GitHub login in the system keychain (service copilot-cli), which a HOME does "
            "not separate. In an env on macOS it usually finds no keychain and offers to keep the token in that "
            "env's .copilot/config.json: answer y. On Linux desktops and Windows the envs share the keychain."
        ),
        # The ~200 MB package it unpacks on first run, and its updates, live in
        # the cache; sharing it spares every env its own copy.
        runtime=("Library/Caches/copilot", ".cache/copilot"),
        letter="P",
        color="teal",
        examples="e.g. --model claude-sonnet-5.5   or   --continue",
    ),
    AgentSpec(
        key="agy",
        title="Google Antigravity CLI",
        binary="agy",
        install="curl -fsSL https://antigravity.google/cli/install.sh | bash",
        docs="https://antigravity.google/docs/cli/reference/",
        adapter="clippet.adapters.antigravity:AntigravityAdapter",
        config_paths=(".gemini/antigravity-cli",),
        # ~/.gemini/config is shared with the Antigravity app; GEMINI.md,
        # AGENTS.md and skills with Gemini CLI.
        seed=SeedSpec(
            entries=(
                ".gemini/antigravity-cli/settings.json",
                ".gemini/antigravity-cli/keybindings.json",
                ".gemini/antigravity-cli/skills",
                ".gemini/config",
                ".gemini/skills",
                ".gemini/GEMINI.md",
                ".gemini/AGENTS.md",
            ),
            credentials=(
                ".gemini/antigravity-cli/antigravity-oauth-token",
                ".gemini/antigravity-cli/mcp_oauth_tokens.json",
                ".gemini/antigravity/mcp_oauth_tokens.json",
            ),
            # "gemini" means: log in with the GEMINI_API_KEY of the environment,
            # and agy refuses to start where that is unset
            scrub={".gemini/antigravity-cli/settings.json": ("modelProvider",)},
        ),
        identity_env=(
            "GEMINI_API_KEY",
            "GOOGLE_GEMINI_BASE_URL",
            "AGY_ADC_AUTH",
            "GOOGLE_CLOUD_QUOTA_PROJECT",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ),
        resume=("--conversation", "{id}"),
        login="/login",
        keychain=(
            "Antigravity keeps one login per machine in the system keyring. In an env on macOS (and on Linux "
            "over SSH) it keeps the login in a file in the env instead, so each env has its own; on Linux "
            "desktops and Windows, logging in or out in any env changes it everywhere."
        ),
        # its binary is shared (~/.local/bin/agy) but each HOME has its own
        # update lock, so envs could rewrite it at the same time
        run_env={"AGY_CLI_DISABLE_AUTO_UPDATE": "true"},
        letter="A",
        color="gold",
        examples="e.g. -c   or   --model <a slug that agy models lists>",
    ),
)

#: Every agent CLIppet can launch, by key, in the order pickers show them.
AGENTS: dict[str, AgentSpec] = {spec.key: spec for spec in _SPECS}

#: The keys of :data:`AGENTS`.
AGENT_KEYS: tuple[str, ...] = tuple(AGENTS)

#: Other names accepted for an agent (``claude_code`` is what config detection says).
ALIASES: dict[str, str] = {"claude_code": "claude"}


def agent_spec(name: str | None) -> AgentSpec | None:
    """The agent called *name* (or one of its aliases), or None."""

    if not name:
        return None
    return AGENTS.get(ALIASES.get(name, name))


def agent_list(separator: str = ", ") -> str:
    """The agent keys, for messages: ``codex, claude, gemini, …``."""

    return separator.join(AGENT_KEYS)
