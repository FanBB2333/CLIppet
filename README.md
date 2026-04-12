# CLIppet

A unified Python adapter framework for orchestrating heterogeneous CLI AI agents.

CLIppet turns scattered, stateless CLI tools — Claude Code, Codex, QoderCLI, Gemini CLI, and others — into strongly-typed, structured-output Python objects with unified execution, isolation, and credential management.

![CLIppet overview](resources/clippet_intro.png)

---

## 1. Quick Start — CLI Config Switching

The fastest way to use CLIppet is to pass a credential file and drop into an interactive agent session. No Python code required. CLIppet infers which terminal to launch from the config file format.

**Claude** (native `claude` config):

```bash
clippet -c /path/to/claude-settings.json
```

**Codex** (auth file only — model config is read from `~/.codex/config.toml`):

```bash
clippet -c /path/to/auth.json
```

**Codex with an explicit `config.toml`** (model, provider, base URL):

```bash
clippet -c /path/to/auth.json --codex-config /path/to/config.toml
```

**Gemini CLI** (uses `.env` file for configuration):

```bash
clippet -c /path/to/gemini.env
```

Both files are injected into an isolated sandbox so your real config is never touched.

---

## 2. Launch Agents with Codex/QoderCLI

CLIppet supports launching interactive sessions with Codex and QoderCLI directly:

**Launch Codex interactively:**

```bash
clippet codex
clippet -c /path/to/auth.json codex
```

**Launch QoderCLI interactively:**

```bash
clippet qodercli
clippet qodercli -p "Explain the current codebase"
```

**Non-interactive mode** — add `-p` to run once and print the result:

```bash
clippet -c /path/to/claude-settings.json -p "What model are you?"
clippet -c /path/to/auth.json --codex-config /path/to/config.toml -p "say hi"
clippet qodercli -p "Analyze this function" --max-turns 25
```

---

## 3. Named Environments

Save a config path under a short alias so you don't have to type the full path:

```bash
clippet env add mycodex /path/to/auth.json
clippet -e mycodex
clippet env list
```

If the saved path points to a native Claude/Codex/Gemini config, CLIppet auto-detects the agent. If it points to a CLIppet composite config, the same composite selection rules below apply.

---

## 4. Project-level `.clippet.json`

For teams that want to run `clippet codex` or `clippet claude` directly inside a project directory without passing `-c` every time, CLIppet supports a project-level `.clippet.json` file.

**Quick start:**

1. Create `.clippet.json` at your project root:

```json
{
  "version": 1,
  "agents": {
    "claude": {
      "config_path": ".clippet.local/claude-settings.json"
    },
    "codex": {
      "config_path": ".clippet.local/codex-auth.json",
      "codex_config_path": ".clippet.local/codex-config.toml"
    },
    "qodercli": {
      "model": "auto"
    },
    "gemini": {
      "config_path": ".clippet.local/gemini.env"
    }
  }
}
```

2. Create `.clippet.local/` and add your credential files there.

3. Add `.clippet.local/` to `.gitignore` (CLIppet does this automatically for new projects).

4. Run:

```bash
clippet codex           # Interactive Codex session
clippet claude          # Interactive Claude session
clippet qodercli        # Interactive QoderCLI session
clippet gemini          # Interactive Gemini CLI session
clippet codex -p "hi"   # Non-interactive with prompt
```

**Security rules:**

- `.clippet.json` stores only **file references**, never API keys or tokens directly.
- `.clippet.local/` must be gitignored because it may contain machine-specific credentials.
- Relative paths must stay inside the project root. Use absolute paths (or `~`-expanded paths like `~/.codex/auth.json`) to reference files outside the project.
- Discovery stops at the nearest Git root — CLIppet will not pick up `.clippet.json` files from unrelated parent directories.

**Path resolution:**

| Path style | Behavior |
|------------|----------|
| `.clippet.local/auth.json` | Relative to project root (where `.clippet.json` lives) |
| `~/.codex/auth.json` | Expanded to user home directory |
| `/absolute/path/to/auth.json` | Used as-is |
| `../escape.json` | **Rejected** — use absolute path instead |

**Precedence:**

Explicit flags always override project-level config:

```bash
clippet -c /explicit/auth.json codex   # Uses explicit path, ignores .clippet.json
clippet codex                           # Uses .clippet.json
```

---

## 5. Why CLIppet?

CLI AI agents are powerful but painful to integrate programmatically:

- **No standard interface** — each tool has its own flags, output formats, and execution models
- **Black-box outputs** — extracting tool calls or intermediate state requires fragile regex or LLM-based post-processing
- **No isolation** — running multiple agents concurrently with different credentials is error-prone
- **No orchestration** — coordinating parallel agent execution requires custom boilerplate every time

CLIppet solves all of this with a single `adapter.run(request)` call.

---

## 6. Features

- **Unified Protocol** — consistent `run()` / `run_async()` interface across all adapters
- **Structured Output** — tool call records, execution time, step counts — no regex hacks
- **Sandbox Isolation** — per-run credential injection, environment variable filtering, temporary workspaces
- **Parallel Orchestration** — `ClippetRunner` dispatches multiple agents concurrently via thread pool or asyncio
- **Declarative Config** — YAML/JSON-based adapter registration with credential profiles
- **Built-in Adapters** — Claude Code, Codex, QoderCLI, and Gemini CLI out of the box; extensible via `BaseSubprocessAdapter`

---

## 7. Installation

```bash
pip install clippet
```

For development:

```bash
pip install clippet[dev]
```

> Requires Python 3.10+

---

## 8. Python SDK Quick Start

```python
from clippet import ClippetRunner, AgentRequest
from clippet.adapters import ClaudeAdapter

# Initialize runner and register adapter
runner = ClippetRunner(max_workers=4)
runner.register("claude", ClaudeAdapter(model="sonnet"))

# Create a request
request = AgentRequest(
    task_prompt="Find and fix the bug in src/server.py",
    workspace_dir="/path/to/project",
    timeout=900,
)

# Execute
result = runner.execute("claude", request)

print(f"Success: {result.is_success}")
print(f"Time: {result.execution_time}s")
print(f"Tools used: {[t.tool_name for t in result.tool_calls]}")
```

---

## 9. Advanced CLI Usage

### 9.1 CLIppet composite config (advanced)

When you need multiple adapters with shared credential profiles, use a CLIppet YAML/JSON config file:

```bash
clippet -c single-adapter-config.json
clippet -c clippet-config.json codex-default -p "review the current repository"
```

Rules:

- If the composite config has exactly one adapter, CLIppet selects it automatically.
- If the composite config has multiple adapters, pass the adapter `name` from the config file.
- Second-home directory mode still requires an explicit trailing `claude` or `codex`.

See the [Configuration](#12-configuration-yaml-or-json) section for the full schema.

### 9.2 Behavior summary

| Flag | Meaning |
|------|---------|
| `-c <file>` | Native agent config (Claude JSON, Codex auth.json, Gemini .env) or CLIppet composite config |
| `--codex-config <file>` | Codex `config.toml` (model / provider). Can be combined with `-c` or used alone |
| `-e <name>` | Resolve a saved environment alias to a config path |
| `-p <prompt>` | Run once non-interactively; omit to launch interactive session |
| `[agent]` | Optional native agent type or composite adapter name. Native configs and single-adapter composite configs can omit it |

---

## 10. Parallel Execution

Run multiple agents on the same task simultaneously:

```python
from clippet import ClippetRunner, AgentRequest
from clippet.adapters import ClaudeAdapter, CodexAdapter

runner = ClippetRunner(max_workers=4)
runner.register("claude", ClaudeAdapter(model="sonnet"))
runner.register("codex", CodexAdapter(model="o4-mini"))

request = AgentRequest(
    task_prompt="Refactor the auth module",
    workspace_dir="/path/to/project",
)

results = runner.execute_parallel({
    "claude": request,
    "codex": request,
})

for name, result in results.items():
    print(f"{name}: {'pass' if result.is_success else 'fail'} in {result.execution_time:.1f}s")
```

---

## 11. Credential Isolation

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

---

## 12. Configuration (YAML or JSON)

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
                "model": "opus"
            }
        },
        {
            "adapter_type": "codex",
            "name": "codex-default",
            "options": {
                "model": "o4-mini"
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

## 13. How It Works

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                          CLIppet                                │
  │                                                                 │
  │   CLI: clippet -c auth.json --codex-config cfg.toml             │
  │   API: runner.execute("codex", AgentRequest(...))               │
  │                          │                                      │
  │                          ▼                                      │
  │          ┌───────────────────────────────┐                      │
  │          │     Config / Auto-detection   │                      │
  │          │  Claude · Codex · QoderCLI    │                      │
  │          │  Gemini · YAML composite      │                      │
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
  │          │  • temp HOME directory        │                      │
  │          │  • inject credential files    │                      │
  │          │  • env var overrides          │                      │
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
           │  is_success · tool_calls · time │
           └─────────────────────────────────┘
```

- **Config detection** auto-identifies file format (Claude JSON / Codex auth.json / Gemini .env / CLIppet YAML) so you rarely need to specify the type explicitly.
- **Adapter layer** translates a generic `AgentRequest` into tool-specific CLI flags and parses the raw output back into a structured `AgentResult`.
- **Sandbox** creates an isolated `$HOME` per run, injects credential files and env vars, then cleans up — your real config is never modified.
- **ClippetRunner** wraps multiple adapters and dispatches them in parallel via a thread pool or asyncio.

---

## 14. Architecture

```
clippet/
├── __init__.py          # Public API
├── models.py            # AgentRequest, AgentResult, IsolationConfig
├── protocols.py         # ClippetAdapter protocol
├── orchestrator.py      # ClippetRunner (parallel dispatch)
├── isolation.py         # Sandbox environments & credential providers
├── adapters/
│   ├── base.py          # BaseSubprocessAdapter
│   ├── claude.py        # Claude Code CLI adapter
│   ├── codex.py         # Codex CLI adapter
│   ├── qodercli.py      # QoderCLI adapter
│   └── gemini.py        # Gemini CLI adapter
├── parsers/
│   └── extractors.py    # JSON/text output parsers
└── config/
    └── registry.py      # YAML/JSON config & adapter registration
```

### 14.1 Core Data Models

| Model | Purpose |
|-------|---------|
| `AgentRequest` | Task prompt, workspace, timeout, model override, isolation config |
| `AgentResult` | Raw output, success flag, execution time, tool calls, step count |
| `ToolCallRecord` | Tool name, parameters, timestamp |
| `IsolationConfig` | Credential files, env overrides, whitelist/blacklist |

### 14.2 Built-in Adapters

| Adapter | CLI Tool | Key Flags |
|---------|----------|-----------|
| `ClaudeAdapter` | `claude` | `--model`, `--permission-mode`, `--max-turns`, `--allowed-tools` |
| `CodexAdapter` | `codex exec` | `--model`, `--sandbox`, `--full-auto`, `--cd`, `--add-dir` |
| `QoderCLIAdapter` | `qodercli` | `-p`, `--model`, `--max-turns`, `--workspace` |
| `GeminiAdapter` | `gemini` | `--model`, config via `.env` file |

### 14.3 Extending with Custom Adapters

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
            is_success=return_code == 0,
            execution_time=0.0,
            tool_calls=[],
            steps_count=0,
        )
```

---

## 15. Development

```bash
git clone https://github.com/FanBB2333/CLIppet.git
cd CLIppet
pip install -e ".[dev]"
pytest
```

---

## 16. License

MIT
