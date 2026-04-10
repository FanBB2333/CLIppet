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

### Launch Claude interactively with a native Claude config

```bash
clippet -c /home/l1ght/repos/CLIppet/glm4-7-base.json claude
```

If `clippet` is not installed as a shell command yet, run it as a module:

```bash
python3 -m clippet.cli -c /home/l1ght/repos/CLIppet/glm4-7-base.json claude
```

This starts the real `claude` interactive session and injects the selected JSON
file as an isolated `~/.claude/settings.json`.

### Run Claude non-interactively with a native Claude config

```bash
clippet -c /home/l1ght/repos/CLIppet/glm4-7-base.json claude -p "你是什么模型，只回答模型名"
```

### Launch Codex interactively with a native Codex config

```bash
clippet -c /path/to/auth.json codex
```

### Register and reuse named environments

Use `-e` when you want to refer to a saved config path by name:

```bash
clippet env add glm47 /home/l1ght/repos/CLIppet/glm4-7-base.json
clippet -e glm47 claude
```

You can inspect registered environments with:

```bash
clippet env list
```

### Use a CLIppet composite config

When the config file is a CLIppet JSON/YAML config containing multiple adapters,
specify which agent to launch:

```bash
clippet -c clippet-config.json claude
clippet -c clippet-config.json codex
```

For non-interactive execution with a composite config:

```bash
clippet -c clippet-config.json claude -p "review the current repository"
```

### Behavior summary

- `-c` accepts a native Claude config, a native Codex config, or a CLIppet composite config.
- `-e` resolves a previously registered environment name to a config path.
- With `-p`, CLIppet runs the agent non-interactively and prints the result.
- Without `-p`, CLIppet launches the underlying CLI in interactive mode.

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
| `CodexAdapter` | `codex exec` | `--model`, `--sandbox`, `--ask-for-approval` |
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
