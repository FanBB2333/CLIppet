<img src="resources/logo.svg" width="72" alt="CLIppet">

# CLIppet

CLIppet runs coding-agent CLIs (Codex, Claude Code, Qoder CLI, pi, GitHub Copilot CLI and Google Antigravity CLI) as whichever account you choose, several at a time, from the terminal, a terminal UI or Python.

![CLIppet: one CLI to orchestrate them all](resources/banner.svg)

## What it does

- **Envs**: each env is a folder with its own logins, sessions and history, so switching accounts means switching envs, and two accounts can run at the same time.
- **Codex accounts**: import them from Cockpit Tools, sub2api, CLIProxyAPI or `auth.json`. A linked env pulls the newest login before every launch.
- **Structured results**: each agent's JSON events become one `AgentResult` with the answer, tool calls, token usage and session id.
- **Parallel runs**: send one prompt to many envs from the terminal UI, or dispatch agents with `ClippetRunner` in Python.
- **Third-party APIs**: `clippet env endpoint` points an agent in an env at a relay, a compatible provider or a local server.

## Install

CLIppet is not on PyPI yet, and it needs Python 3.10+.

```bash
pip install "clippet[tui,cockpit] @ git+https://github.com/FanBB2333/CLIppet.git"
```

`tui` adds the terminal UI and `cockpit` reads the Cockpit Tools account store. CLIppet starts the agent CLIs you already have, so install only the ones you want to run.

## Quick start

```bash
clippet env create work --from-current    # copy your agent configuration, not your logins
clippet -e work codex login               # log in once inside the env
clippet -e work codex                     # from now on, always this account
```

Do the same with another name for a second account. Two envs can run at the same time.

For a one-off run, point `-c` at a credential file. It goes into a throw-away env:

```bash
clippet -c /path/to/auth.json                # interactive
clippet -c /path/to/auth.json -p "say hi"    # once, headless, prints the answer
```

Arguments after the agent name go to the agent unchanged, such as `clippet -e work codex resume --last`. The one exception is a `-p` directly after the agent name, which is CLIppet's own ([details](docs/guide.md#33-passing-arguments-through)).

## Supported agents

| Agent | Command | In an env |
|-------|---------|-----------|
| Codex | `codex` | your `$HOME`; its config in `<env>/.codex` (`CODEX_HOME`) |
| pi | `pi` | your `$HOME`; its config in `<env>/.pi/agent` (`PI_CODING_AGENT_DIR`) |
| Qoder CLI | `qodercli` | your `$HOME`; its config in `<env>/.qoder` (`QODER_CONFIG_DIR`) |
| Claude Code | `claude` | the env is its `$HOME` |
| GitHub Copilot CLI | `copilot` | the env is its `$HOME` |
| Google Antigravity CLI | `agy` | the env is its `$HOME` |

Keeping your `$HOME` means git, SSH and the keychain work as usual. Envs separate logins and configuration; they are not a security sandbox. See [how isolation works, and its limits](docs/guide.md#34-how-isolation-works-and-its-limits).

## Codex accounts

```bash
clippet auth list cockpit                        # what does Cockpit Tools hold?
clippet env import acct-a cockpit --account 2    # put one account into an env
clippet -e acct-a codex                          # Codex, logged in as that account
```

CLIppet also reads Cockpit Tools exports, sub2api and CLIProxyAPI files, `auth.json` and token bundles ([all sources](docs/guide.md#32-importing-codex-accounts-from-other-tools)).

## Terminal UI

`clippet tui`, or plain `clippet` in a terminal, shows every env with its Codex account and remaining quota, launches agents and streams headless runs. It needs the `tui` extra.

![clippet tui: the Envs place](resources/tui-envs.svg)

## Python

```python
from clippet import ClippetRunner, AgentRequest
from clippet.adapters import CodexAdapter

runner = ClippetRunner(max_workers=4)
runner.register("codex", CodexAdapter(sandbox="workspace-write"))

request = AgentRequest(task_prompt="Find and fix the bug in src/server.py", workspace_dir="/path/to/project")
result = runner.execute("codex", request)

print(result.is_success, result.output_text, result.usage)
```

## Documentation

The [guide](docs/guide.md) has the full reference:

- [Envs: what `--from-current` copies and how to manage them](docs/guide.md#31-home-containers-conda-style-envs)
- [Third-party APIs](docs/guide.md#36-third-party-apis-relays-compatible-providers-local-models)
- [The Codex app, one instance per env (macOS)](docs/guide.md#37-the-codex-app-one-instance-per-env-macos)
- [Pinning a project to an env with `.clippet.json`](docs/guide.md#4-project-level-clippetjson)
- [Terminal UI keys](docs/guide.md#2-terminal-ui)
- [Python SDK](docs/guide.md#8-python-sdk-quick-start), [parallel runs](docs/guide.md#10-parallel-execution) and [YAML or JSON configuration](docs/guide.md#12-configuration-yaml-or-json)
- [Architecture and custom adapters](docs/guide.md#14-architecture)

## Development

```bash
git clone https://github.com/FanBB2333/CLIppet.git
cd CLIppet
pip install -e ".[dev,cockpit,tui]"
pytest
```

The [guide](docs/guide.md#15-development) explains how the drawings and the website are generated.

## License

[MIT](LICENSE)
