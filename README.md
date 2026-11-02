# CLIppet

A unified Python adapter framework for orchestrating heterogeneous CLI AI agents.

CLIppet turns scattered, stateless CLI tools — Claude Code, Codex, QoderCLI, Gemini CLI, and others — into strongly-typed, structured-output Python objects with unified execution, isolation, and credential management.

![CLIppet overview](resources/clippet_intro.png)

CLIppet is an **agent execution layer**, not just an account switcher: every run picks an *identity* (a persistent HOME container, a credential file, or your real HOME) per process, so several agents and several accounts can run side by side — from the terminal or from Python.

---

## 1. Quick Start

Point CLIppet at a credential/config file and drop into an interactive session. The agent is inferred from the file format, and the file is injected into a throw-away HOME, so your real config stays untouched.

```bash
clippet -c /path/to/claude-settings.json          # Claude Code (settings.json with env)
clippet -c /path/to/auth.json                     # Codex (auth.json — ChatGPT login or API key)
clippet -c /path/to/auth.json --codex-config cfg.toml   # Codex + its own config.toml
clippet -c /path/to/gemini.env                    # Gemini CLI (.env)
```

Codex accepts credentials in **any common account-manager format** — not only `auth.json`:

```bash
clippet -c cockpit --account 2 codex                       # Cockpit Tools' live account store
clippet -c cockpit_auto_backup_full_2026-11-02.json --account bob@example.com codex
clippet -c sub2api-account-20261102.json codex             # sub2api account export
clippet -c ~/.cli-proxy-api/codex-xxxx-me@example.com-plus.json codex   # CLIProxyAPI
```

Add `-p` to run once headlessly and print the answer:

```bash
clippet -c /path/to/auth.json -p "say hi"
clippet codex -p "Explain the current codebase"
```

Everything after the agent name is **passed through** to the agent's own CLI:

```bash
clippet -e work codex resume --last        # → codex resume --last   (inside env "work")
clippet -e work codex login                # log the env into its own account
clippet -e work claude --continue
clippet -e work codex -c model_reasoning_effort=high
```

---

## 2. Accounts & Environments

### 2.1 HOME containers (conda-style envs)

A HOME env is a persistent fake `$HOME` at `~/.clippet/envs/<name>/`. CLIppet launches the agent with that directory **as** `$HOME`, so its login, sessions, MCP state and history persist across runs — and stay separate from every other env. Two envs can run at the same time with two different accounts.

```bash
clippet env create work --from-current     # copy your agent *configuration* (no logins)
clippet -e work codex login                # log in once inside the env
clippet -e work codex                      # from now on: always this account
clippet -e personal codex &  clippet -e work codex &   # parallel, fully isolated
```

What `--from-current` copies — and what it deliberately does not:

| Copied (configuration) | Not copied |
|------------------------|------------|
| `~/.codex/config.toml`, `AGENTS.md`, `skills/`, `prompts/`, `rules/`, `hooks.json` | sessions, history, logs, sqlite DBs, caches, sockets |
| `~/.claude/settings.json`, `CLAUDE.md`, `agents/`, `commands/`, `skills/`, `~/.claude.json` (MCP; account keys stripped) | `~/.claude/projects` transcripts |
| `~/.gemini/settings.json`, `GEMINI.md`, `commands/`, `extensions/` | |
| **Credentials only with `--with-auth`**: `.codex/auth.json`, `.claude/.credentials.json`, `.gemini/.env`, `oauth_creds.json` | |

> **Why logins are not copied by default** — OpenAI (and Anthropic) rotate OAuth refresh tokens: the first copy that refreshes invalidates every other copy, and reusing an old refresh token can revoke the whole login. For parallel long-term use, log in separately inside each env. `--with-auth` is there when you knowingly want a copy.

New envs also **link** (not copy) `~/.gitconfig` and `~/.config/git` from your real HOME, so `git commit` inside an agent session keeps your identity. Change this with `--share PATH` (repeatable) or `--no-share`.

```bash
clippet env list                     # envs with the Codex account each one carries
clippet env status [name]            # detailed identity: account, plan, token expiry, source
clippet env path work                # print the HOME path
clippet env clone work scratch       # duplicate (sessions included, logins excluded unless --with-auth)
clippet env remove scratch           # unregister, keep the directory
clippet env remove scratch --purge   # also delete ~/.clippet/envs/scratch/
```

Running bare `clippet codex` (or `claude` / `gemini` / `qodercli`) in a terminal opens a small picker listing `base` (your real HOME) and every env, with the Codex account of each.

### 2.2 Importing Codex accounts from other tools

`clippet env import` stores one account from any supported source in an env (creating it, with your Codex config but no other logins, if needed):

```bash
clippet auth list cockpit                         # what does Cockpit Tools hold?
clippet env import acct-a cockpit --account 2     # linked to Cockpit (default for 'cockpit')
clippet env import acct-b ./backup.json --account bob@example.com
clippet -e acct-a codex                           # → Codex logged in as that account
```

Supported Codex credential sources:

| Source | How to pass it | Notes |
|--------|----------------|-------|
| Codex `auth.json` | path to the file, a HOME dir, or a `CODEX_HOME` dir | ChatGPT login, API key, PAT / agent-identity files are passed through verbatim |
| Cockpit Tools live store | `cockpit`, `cockpit:<dir>`, or the data dir (`~/.antigravity_cockpit`) | read-only; decrypting needs `pip install 'clippet[cockpit]'` |
| Cockpit Tools export / auto-backup | `cockpit-tools.data-transfer` / `account-transfer` JSON | only the Codex platform is read |
| sub2api account export | `sub2api-account-*.json` | only `platform: openai` accounts; Claude/Gemini entries are ignored |
| CLIProxyAPI token file | `codex-*.json` (`"type": "codex"`) | the separate `siwc` token set is never used |
| Token bundle | `{"id_token", "access_token", "refresh_token"}` (also nested in `tokens` / `credentials`) | the access token must be an OpenAI JWT |
| JSON arrays / JSON Lines | of any of the above | |

Select one account with `--account`: a 1-based index from `clippet auth list`, an e-mail, the e-mail's local part, a label or an account id. Accounts the source marks as needing re-login, ChatGPT web-session tokens and non-OpenAI tokens are flagged or skipped.

**Linked envs** (`--link`, the default for the Cockpit store) pull the newest login from the source before every launch, so the account manager stays the one that refreshes tokens. If Codex ever refreshes inside the env first, CLIppet keeps the env's newer login and warns you that the source now holds a superseded refresh token. `clippet env sync <name>` pulls on demand.

Other conversions:

```bash
clippet auth export cockpit --account 2 -o ./acct2-auth.json   # native auth.json (0600)
```

**Temporary runs** (`-c <source>`) convert the credential into a throw-away HOME. If Codex refreshes the tokens during the run, the new login is not lost: for native `auth.json` sources it is written back to that file; for converted sources it is saved under `~/.clippet/refreshed/` and CLIppet tells you where.

Using a **sub2api (or any OpenAI-compatible) gateway key** works with the regular two-file mode:

```bash
echo '{"OPENAI_API_KEY": "<gateway key>"}' > gw-auth.json
cat > gw.toml <<'EOF'
model_provider = "OpenAI"
model = "gpt-5.5"
[model_providers.OpenAI]
name = "OpenAI"
base_url = "https://<gateway host>/v1"
wire_api = "responses"
requires_openai_auth = true
EOF
clippet -c gw-auth.json --codex-config gw.toml codex
```

### 2.3 Passing arguments through

```
clippet [CLIPPET OPTIONS] [AGENT] [AGENT ARGS...]
```

- CLIppet options (`-c`, `-e`, `--account`, `--codex-config`, `-p`) go **before** the agent name.
- Everything after the agent name goes to the agent CLI unchanged, including subcommands (`resume`, `login`, `mcp`, ...) and the agent's own `-c`.
- One exception keeps `clippet codex -p "task"` working: a `-p/--prompt` *directly* after the agent name is CLIppet's headless prompt. Use `--` to forward even that: `clippet -e work claude -- -p "hi" --output-format stream-json`.
- With pass-through arguments CLIppet never reads stdin, so `clippet -e work codex login --with-api-key < key.txt` works.
- In headless mode (`-p`) the extra arguments are added to the agent's headless command (e.g. `codex exec`).

Interactive launches only add the flags you configured — CLIppet does not inject a model, sandbox or permission mode of its own.

### 2.4 How isolation works (and its limits)

- `$HOME` points at the env (or a temp dir). Variables that would redirect an agent elsewhere are removed from the inherited environment: `CODEX_HOME`, `CODEX_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` (Codex), `ANTHROPIC_*`, `CLAUDE_CONFIG_DIR` (Claude), `GEMINI_*` / `GOOGLE_*` credentials (Gemini) and `XDG_CONFIG_HOME` / `XDG_DATA_HOME` / `XDG_STATE_HOME` / `XDG_CACHE_HOME`. Values from your credential file or `env_overrides` are applied afterwards. Opt out in Python with `IsolationConfig(scrub_agent_env=False)`.
- When CLIppet injects a Codex `auth.json`, it also passes `-c cli_auth_credentials_store="file"` so a `config.toml` that selects the OS keyring cannot hide it.
- This is HOME/environment isolation, not a security sandbox: the agent can still read files outside its workspace, and credential stores keyed by absolute paths (e.g. the macOS Keychain) are only as separate as the tool makes them.
- `CLIPPET_HOME` relocates `~/.clippet` (registry and envs).

### 2.5 File-alias envs (legacy)

A short name for an existing config file. CLIppet copies that one file into a fresh temp HOME on each run — no session persistence, suitable for one-shot scripted calls.

```bash
clippet env add mycodex /path/to/auth.json
clippet -e mycodex -p "say hi"
```

---

## 3. Project-level `.clippet.json`

Run `clippet codex` / `claude` / `gemini` / `qodercli` inside a project without passing `-c` every time. A project can either pin a **named env** (so it always runs under the right account) or reference credential files:

```json
{
  "version": 1,
  "agents": {
    "codex":    { "env": "work" },
    "claude":   { "config_path": ".clippet.local/claude-settings.json" },
    "gemini":   { "config_path": ".clippet.local/gemini.env" },
    "qodercli": { "env": "work" }
  }
}
```

Codex can also use files, including multi-account sources:

```json
{
  "version": 1,
  "agents": {
    "codex": {
      "config_path": ".clippet.local/codex-auth.json",
      "codex_config_path": ".clippet.local/codex-config.toml",
      "account": "work@example.com"
    }
  }
}
```

**Security rules:**

- `.clippet.json` stores only **references** (env names, file paths), never API keys or tokens — unknown fields are rejected.
- `.clippet.local/` must be gitignored because it may contain machine-specific credentials.
- Relative paths must stay inside the project root. Use absolute paths (or `~`-expanded paths like `~/.codex/auth.json`) to reference files outside the project.
- Discovery stops at the nearest Git root — CLIppet will not pick up `.clippet.json` files from unrelated parent directories.
- `env` and file paths are mutually exclusive per agent.

**Path resolution:**

| Path style | Behavior |
|------------|----------|
| `.clippet.local/auth.json` | Relative to project root (where `.clippet.json` lives) |
| `~/.codex/auth.json` | Expanded to user home directory |
| `/absolute/path/to/auth.json` | Used as-is |
| `cockpit` / `cockpit:<dir>` | The Cockpit Tools account store |
| `../escape.json` | **Rejected** — use absolute path instead |

**Precedence:** explicit flags always override project-level config:

```bash
clippet -c /explicit/auth.json codex   # Uses explicit path, ignores .clippet.json
clippet codex                          # Uses .clippet.json
```

---

## 4. Why CLIppet?

CLI AI agents are powerful but painful to integrate programmatically:

- **No standard interface** — each tool has its own flags, output formats, and execution models
- **Black-box outputs** — extracting tool calls or intermediate state requires fragile regex or LLM-based post-processing
- **No isolation** — running multiple agents concurrently with different credentials is error-prone
- **No orchestration** — coordinating parallel agent execution requires custom boilerplate every time

CLIppet solves all of this with a single `adapter.run(request)` call — and the identity of every run is explicit.

---

## 5. Features

- **Unified Protocol** — consistent `run()` / `run_async()` interface across all adapters
- **Structured Output** — final answer, tool calls, step count, token usage and session id (Codex via `codex exec --json`, Claude via JSON output)
- **Identity layer** — persistent HOME envs, multi-format Codex credentials (Codex, Cockpit Tools, sub2api, CLIProxyAPI), linked imports, refreshed-token write-back
- **Sandbox Isolation** — per-run credential injection, identity env-var scrubbing, temporary workspaces
- **Parallel Orchestration** — `ClippetRunner` dispatches multiple agents concurrently via thread pool or asyncio
- **Declarative Config** — YAML/JSON-based adapter registration with credential profiles; per-project `.clippet.json`
- **Built-in Adapters** — Claude Code, Codex, QoderCLI, and Gemini CLI out of the box; extensible via `BaseSubprocessAdapter`

---

## 6. Installation

```bash
pip install clippet
pip install 'clippet[cockpit]'   # + reading the Cockpit Tools account store
pip install 'clippet[api]'       # + OpenAIAdapter
```

For development:

```bash
pip install 'clippet[dev]'
```

> Requires Python 3.10+

---

## 7. Python SDK Quick Start

```python
from clippet import ClippetRunner, AgentRequest
from clippet.adapters import CodexAdapter

runner = ClippetRunner(max_workers=4)
runner.register("codex", CodexAdapter(sandbox="workspace-write"))

request = AgentRequest(
    task_prompt="Find and fix the bug in src/server.py",
    workspace_dir="/path/to/project",
    timeout=900,
)

result = runner.execute("codex", request)

print(f"Success: {result.is_success} in {result.execution_time:.1f}s")
print(f"Answer:  {result.output_text}")
print(f"Tools:   {[t.tool_name for t in result.tool_calls]}")
print(f"Usage:   {result.usage}")          # e.g. {'input_tokens': ..., 'output_tokens': ...}
print(f"Thread:  {result.session_id}")
```

Adapters only pass the options you set; with no `model` the agent uses the model configured in its HOME.

---

## 8. Advanced CLI Usage

### 8.1 CLIppet composite config (advanced)

When you need multiple adapters with shared credential profiles, use a CLIppet YAML/JSON config file:

```bash
clippet -c single-adapter-config.json
clippet -c clippet-config.json codex-default -p "review the current repository"
```

Rules:

- If the composite config has exactly one adapter, CLIppet selects it automatically.
- If the composite config has multiple adapters, pass the adapter `name` from the config file.
- Second-home directory mode still requires an explicit agent (`claude`, `codex`, `gemini`, `qodercli`).

See the [Configuration](#11-configuration-yaml-or-json) section for the full schema.

### 8.2 Behavior summary

| Flag | Meaning |
|------|---------|
| `-c <file>` | Claude settings JSON, Codex credential (any supported format), Gemini `.env`, CLIppet composite config, a HOME directory, or `cockpit` |
| `--codex-config <file>` | Codex `config.toml` (model / provider). Can be combined with `-c` or used alone |
| `--account <sel>` | Pick one account from a multi-account credential source |
| `-e <name>` | A saved environment — a HOME container (default) or a file alias (legacy) |
| `-p <prompt>` | Run once non-interactively; omit to launch an interactive session |
| `[agent]` | Native agent type or composite adapter name. Native configs and single-adapter composite configs can omit it |
| `[agent args...]` | Passed through to the agent CLI (see 2.3) |

---

## 9. Parallel Execution

Run multiple agents — or the same agent under several accounts — simultaneously:

```python
from clippet import ClippetRunner, AgentRequest, get_environment
from clippet.config.detector import create_adapter_with_second_home

runner = ClippetRunner(max_workers=2)
for env in ("acct-a", "acct-b"):          # two HOME envs, two Codex accounts
    home = get_environment(env)["home_dir"]
    runner.register(env, create_adapter_with_second_home(home, "codex"))

request = AgentRequest(task_prompt="Refactor the auth module", workspace_dir="/path/to/project")
results = runner.execute_parallel({"acct-a": request, "acct-b": request})

for name, result in results.items():
    print(f"{name}: {'pass' if result.is_success else 'fail'} in {result.execution_time:.1f}s")
```

---

## 10. Credential Isolation

Run agents with different API keys without conflicts:

```python
from clippet import AgentRequest, IsolationConfig

request_a = AgentRequest(
    task_prompt="Fix the login bug",
    workspace_dir="/path/to/project",
    isolation=IsolationConfig(
        env_overrides={"ANTHROPIC_API_KEY": "sk-key-A"},
    ),
)

request_b = AgentRequest(
    task_prompt="Fix the login bug",
    workspace_dir="/path/to/project",
    isolation=IsolationConfig(
        env_overrides={"ANTHROPIC_API_KEY": "sk-key-B"},
    ),
)

results = runner.execute_parallel({
    "agent-a": request_a,
    "agent-b": request_b,
})
```

`IsolationConfig` also accepts `credential_files` (copy files into the sandbox HOME), `credential_contents` (write generated files), `home_dir` (use a persistent HOME), `sync_back` (copy rewritten credential files back after the run) and `env_whitelist` / `env_blacklist`.

---

## 11. Configuration (YAML or JSON)

Define adapters and credential profiles declaratively:

```json
{
    "credential_profiles": {
        "personal": {
            "files": {
                ".claude/settings.json": "~/profiles/personal/settings.json"
            },
            "env": {
                "ANTHROPIC_API_KEY": "${PERSONAL_API_KEY}"
            }
        }
    },
    "adapters": [
        {
            "adapter_type": "claude",
            "name": "claude-personal",
            "options": {
                "model": "opus",
                "credential_profile": "personal"
            }
        },
        {
            "adapter_type": "codex",
            "name": "codex-default",
            "options": {
                "sandbox": "workspace-write"
            }
        },
        {
            "adapter_type": "qodercli",
            "name": "qodercli-default",
            "options": {
                "model": "auto"
            }
        },
        {
            "adapter_type": "gemini",
            "name": "gemini-default",
            "options": {
                "model": "gemini-3.1-pro"
            }
        }
    ],
    "default_timeout": 900,
    "max_workers": 4
}
```

```python
from clippet.config import load_config, create_runner_from_config

config = load_config("clippet-config.json")
runner = create_runner_from_config(config)

result = runner.execute("claude-personal", request)
```

---

## 12. How It Works

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                          CLIppet                                │
  │                                                                 │
  │   CLI: clippet -e work codex resume --last                      │
  │   API: runner.execute("codex", AgentRequest(...))               │
  │                          │                                      │
  │                          ▼                                      │
  │          ┌───────────────────────────────┐                      │
  │          │   Identity / Environment      │                      │
  │          │  HOME envs · credential files │                      │
  │          │  Codex/Cockpit/sub2api/CPA    │                      │
  │          └───────────────┬───────────────┘                      │
  │                          ▼                                      │
  │     ┌────────────────────────────────────────┐                  │
  │     │              Adapter layer             │                  │
  │     │  ClaudeAdapter · CodexAdapter          │                  │
  │     │  QoderCLIAdapter · GeminiAdapter       │                  │
  │     │  build_command() · parse_output()      │                  │
  │     └────────────────────┬───────────────────┘                  │
  │                          ▼                                      │
  │          ┌───────────────────────────────┐                      │
  │          │      Sandbox / Isolation      │                      │
  │          │  • HOME = env or temp dir     │                      │
  │          │  • inject credentials (0600)  │                      │
  │          │  • scrub identity env vars    │                      │
  │          └───────────────┬───────────────┘                      │
  └──────────────────────────┼──────────────────────────────────────┘
                             ▼
           ┌─────────────────────────────────┐
           │      CLI Agent subprocess       │
           │  claude / codex / qodercli      │
           │  gemini                         │
           └─────────────────┬───────────────┘
                             ▼
           ┌─────────────────────────────────┐
           │    Output parser → AgentResult  │
           │  output_text · tool_calls ·     │
           │  usage · session_id · time      │
           └─────────────────────────────────┘
```

- **Identity layer** decides which HOME and credentials a run uses: a HOME env, a credential file in any supported format (converted to the agent's native layout), or the real HOME.
- **Adapter layer** translates a generic `AgentRequest` into tool-specific CLI flags and parses the raw output back into a structured `AgentResult`.
- **Sandbox** sets `$HOME`, injects credential files and env vars, removes identity variables inherited from the parent, and cleans up temp HOMEs — after syncing refreshed logins back.
- **ClippetRunner** wraps multiple adapters and dispatches them in parallel via a thread pool or asyncio.

---

## 13. Architecture

```
clippet/
├── __init__.py          # Public API
├── models.py            # AgentRequest, AgentResult, IsolationConfig
├── protocols.py         # ClippetAdapter protocol
├── orchestrator.py      # ClippetRunner (parallel dispatch)
├── isolation.py         # Sandbox environments, credential providers, env scrubbing
├── auth/
│   ├── codex.py         # Codex credentials in every format → native auth.json
│   ├── cockpit.py       # Cockpit Tools account store (read-only)
│   └── jwt.py           # Local JWT claim decoding
├── adapters/
│   ├── base.py          # BaseSubprocessAdapter
│   ├── claude.py        # Claude Code CLI adapter
│   ├── codex.py         # Codex CLI adapter (+ exec --json event parser)
│   ├── qodercli.py      # QoderCLI adapter
│   └── gemini.py        # Gemini CLI adapter
├── parsers/
│   └── extractors.py    # JSON/text output parsers
└── config/
    ├── detector.py      # Config/credential detection and adapter factories
    ├── environments.py  # HOME envs, seeding, Codex account import/sync
    ├── project.py       # .clippet.json discovery
    └── registry.py      # YAML/JSON config & adapter registration
```

### 13.1 Core Data Models

| Model | Purpose |
|-------|---------|
| `AgentRequest` | Task prompt, workspace, timeout, model override, extra CLI args, isolation config |
| `AgentResult` | Raw output, final `output_text`, success flag, execution time, tool calls, step count, usage, session id |
| `ToolCallRecord` | Tool name, parameters, timestamp |
| `IsolationConfig` | HOME dir, credential files/contents, sync-back, env overrides, whitelist/blacklist, env scrubbing |
| `CodexCredential` | One Codex identity from any source format; renders native `auth.json` |

### 13.2 Built-in Adapters

| Adapter | CLI Tool | Key Flags |
|---------|----------|-----------|
| `ClaudeAdapter` | `claude -p --output-format json` | `--model`, `--permission-mode`, `--max-turns`, `--allowed-tools` |
| `CodexAdapter` | `codex exec --json` | `--model`, `--sandbox`, `--cd`, `--add-dir`, `--skip-git-repo-check`, `-c` |
| `QoderCLIAdapter` | `qodercli` | `-p`, `--model`, `--max-turns`, `-w` (workspace) |
| `GeminiAdapter` | `gemini` | `--model`, config via `.env` file |

### 13.3 Extending with Custom Adapters

Subclass `BaseSubprocessAdapter` and implement two methods:

```python
from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult

class MyAdapter(BaseSubprocessAdapter):
    @property
    def agent_name(self) -> str:
        return "my-agent"

    def build_command(self, request: AgentRequest) -> list[str]:
        return ["my-cli", "--prompt", request.task_prompt]

    def parse_output(self, stdout: str, stderr: str, return_code: int) -> AgentResult:
        return AgentResult(
            raw_output=stdout,
            output_text=stdout.strip(),
            is_success=return_code == 0,
        )
```

---

## 14. Development

```bash
git clone https://github.com/FanBB2333/CLIppet.git
cd CLIppet
pip install -e ".[dev,cockpit]"
pytest
```

---

## 15. License

MIT
