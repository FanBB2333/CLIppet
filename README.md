# CLIppet

A unified Python adapter framework for orchestrating heterogeneous CLI AI agents.

CLIppet turns scattered, stateless CLI tools — Claude Code, Codex, Qoder, and others — into strongly-typed, structured-output Python objects with unified execution, isolation, and credential management.

## Why CLIppet?

CLI AI agents are powerful but painful to integrate programmatically:

- **No standard interface** — each tool has its own flags, output formats, and execution models
- **Black-box outputs** — extracting tool calls or intermediate state requires fragile regex or LLM-based post-processing
- **No isolation** — running multiple agents concurrently with different credentials is error-prone
- **No orchestration** — coordinating parallel agent execution requires custom boilerplate every time

CLIppet solves all of this with a single `adapter.run(request)` call.

## Features

- **Unified Protocol** — consistent `run()` / `run_async()` interface across all adapters
- **Structured Output** — tool call records, execution time, step counts — no regex hacks
- **Sandbox Isolation** — per-run credential injection, environment variable filtering, temporary workspaces
- **Parallel Orchestration** — `ClippetRunner` dispatches multiple agents concurrently via thread pool or asyncio
- **Declarative Config** — YAML/JSON-based adapter registration with credential profiles
- **Built-in Adapters** — Claude Code, Codex, and Qoder out of the box; extensible via `BaseSubprocessAdapter`

## Installation

```bash
pip install clippet
```

For development:

```bash
pip install clippet[dev]
```

> Requires Python 3.10+

## Quick Start

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

## CLI Usage

CLIppet can also be used as a command-line launcher for Claude Code and Codex.

### Interactive mode — the simplest way to get started

The fastest way to use CLIppet is to pass a credential file and drop into an
interactive agent session.  No Python code required.

**Claude** (native `claude` config):

```bash
clippet -c /path/to/claude-settings.json claude
```

**Codex** (auth file only — model config is read from `~/.codex/config.toml`):

```bash
clippet -c /path/to/auth.json codex
```

**Codex with an explicit `config.toml`** (model, provider, base URL):

```bash
clippet -c /path/to/auth.json --codex-config /path/to/config.toml codex
```

Both files are injected into an isolated sandbox `~/.codex/` so your real
config is never touched.  The model is read automatically from `config.toml`;
you do not need to pass `--model` separately.

You can also supply only the `config.toml` — the `auth.json` is then read from
`~/.codex/auth.json`:

```bash
clippet --codex-config /path/to/config.toml codex
```

If `clippet` is not on your `PATH`, run it as a module:

```bash
python3 -m clippet.cli -c /path/to/auth.json --codex-config /path/to/config.toml codex
```

### Non-interactive (single prompt)

Add `-p` to run once and print the result without entering interactive mode:

```bash
clippet -c /path/to/claude-settings.json claude -p "你是什么模型，只回答模型名"
clippet -c /path/to/auth.json --codex-config /path/to/config.toml codex -p "say hi"
```

### Named environments

Save a config path under a short alias so you don't have to type the full path:

```bash
clippet env add mycodex /path/to/auth.json
clippet -e mycodex codex
clippet env list
```

### CLIppet composite config (advanced)

When you need multiple adapters with shared credential profiles, use a CLIppet
YAML/JSON config file:

```bash
clippet -c clippet-config.json claude
clippet -c clippet-config.json codex -p "review the current repository"
```

See the [Configuration](#configuration-yaml-or-json) section for the full schema.

### Behavior summary

| Flag | Meaning |
|------|---------|
| `-c <file>` | Native agent config (Claude JSON, Codex auth.json) or CLIppet composite config |
| `--codex-config <file>` | Codex `config.toml` (model / provider). Can be combined with `-c` or used alone |
| `-e <name>` | Resolve a saved environment alias to a config path |
| `-p <prompt>` | Run once non-interactively; omit to launch interactive session |

## Parallel Execution

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

## Credential Isolation

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

## Configuration (YAML or JSON)

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

## How It Works

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                          CLIppet                                │
  │                                                                 │
  │   CLI: clippet -c auth.json --codex-config cfg.toml codex       │
  │   API: runner.execute("codex", AgentRequest(...))               │
  │                          │                                      │
  │                          ▼                                      │
  │          ┌───────────────────────────────┐                      │
  │          │     Config / Auto-detection   │                      │
  │          │  native Claude · Codex · YAML │                      │
  │          └───────────────┬───────────────┘                      │
  │                          ▼                                      │
  │     ┌────────────────────────────────────────┐                  │
  │     │              Adapter layer             │                  │
  │     │  ClaudeAdapter · CodexAdapter · Qoder  │                  │
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
           │  claude / codex exec / qoder    │
           └─────────────────┬───────────────┘
                             ▼
           ┌─────────────────────────────────┐
           │    Output parser → AgentResult  │
           │  is_success · tool_calls · time │
           └─────────────────────────────────┘
```

- **Config detection** auto-identifies file format (Claude JSON / Codex auth.json / CLIppet YAML) so you rarely need to specify the type explicitly.
- **Adapter layer** translates a generic `AgentRequest` into tool-specific CLI flags and parses the raw output back into a structured `AgentResult`.
- **Sandbox** creates an isolated `$HOME` per run, injects credential files and env vars, then cleans up — your real config is never modified.
- **ClippetRunner** wraps multiple adapters and dispatches them in parallel via a thread pool or asyncio.

## Architecture

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
│   └── qoder.py         # Qoder CLI adapter
├── parsers/
│   └── extractors.py    # JSON/text output parsers
└── config/
    └── registry.py      # YAML/JSON config & adapter registration
```

### Core Data Models

| Model | Purpose |
|-------|---------|
| `AgentRequest` | Task prompt, workspace, timeout, model override, isolation config |
| `AgentResult` | Raw output, success flag, execution time, tool calls, step count |
| `ToolCallRecord` | Tool name, parameters, timestamp |
| `IsolationConfig` | Credential files, env overrides, whitelist/blacklist |

### Built-in Adapters

| Adapter | CLI Tool | Key Flags |
|---------|----------|-----------|
| `ClaudeAdapter` | `claude` | `--model`, `--permission-mode`, `--max-turns`, `--allowed-tools` |
| `CodexAdapter` | `codex exec` | `--model`, `--sandbox`, `--full-auto`, `--cd`, `--add-dir` |
| `QoderAdapter` | `qoder chat` | `--mode`, `-a` (add files), `--profile` |

### Extending with Custom Adapters

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

## Development

```bash
git clone https://github.com/FanBB2333/CLIppet.git
cd CLIppet
pip install -e ".[dev]"
pytest
```

## License

MIT
