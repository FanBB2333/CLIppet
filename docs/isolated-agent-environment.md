# Isolated Agent Environment Design

> 为 CLIppet 提供凭证隔离与环境沙箱能力，使每个 agent 子进程运行在独立的配置/凭证上下文中，互不干扰。

## 动机

CLI agent 工具（Claude Code、Codex 等）默认读取用户主目录下的配置文件来获取凭证和设置：

| Agent | 凭证/配置路径 |
|-------|---------------|
| Claude Code | `~/.claude/settings.json`, `~/.claude/credentials.json` |
| Codex | `~/.codex/auth.json` |
| Qoder | `~/.config/qoder/` 或环境变量 |

当前 `BaseSubprocessAdapter` 使用 `env=None` 启动子进程，直接继承父进程的全部环境变量和文件系统访问权限。这意味着：

- 所有 agent 共享同一套凭证，无法为不同任务使用不同账户/API key
- 修改凭证会影响正在运行的其他 agent
- 无法在不干扰日常工作流的前提下进行测试或切换

## 设计概览

```
┌─────────────────────────────────────┐
│         ClippetRunner               │
│  ┌───────────────────────────────┐  │
│  │    IsolatedEnvironment        │  │
│  │  ┌─────────┐  ┌───────────┐  │  │
│  │  │ sandbox │  │ credential│  │  │
│  │  │  home/  │  │  provider │  │  │
│  │  └────┬────┘  └─────┬─────┘  │  │
│  │       │              │        │  │
│  │       ▼              ▼        │  │
│  │   env override + symlink/copy │  │
│  └───────────────┬───────────────┘  │
│                  │                   │
│         subprocess.Popen(env=...)   │
│                  │                   │
│          ┌───────▼────────┐         │
│          │  Agent Process  │         │
│          │  (isolated HOME)│         │
│          └────────────────┘         │
└─────────────────────────────────────┘
```

## 核心模块

### 1. `IsolatedEnvironment` — 隔离环境上下文管理器

- [ ] 创建临时沙箱目录作为 agent 的虚拟 `$HOME`
- [ ] 支持 context manager 协议（`with IsolatedEnvironment(...) as env:`），退出时自动清理
- [ ] 可选持久化模式（不自动清理，用于调试或复用）

```python
class IsolatedEnvironment:
    """为单个 agent 执行创建隔离的文件系统与环境变量上下文。"""

    def __init__(
        self,
        base_dir: Path | None = None,      # 沙箱根目录，默认 tempfile.mkdtemp
        persist: bool = False,              # 是否保留沙箱目录
        inherit_env: bool = True,           # 是否继承父进程环境变量
        env_overrides: dict[str, str] = {}, # 额外环境变量覆盖
        env_whitelist: list[str] | None = None,  # 仅继承这些环境变量
        env_blacklist: list[str] | None = None,  # 排除这些环境变量
    ): ...

    @property
    def home_dir(self) -> Path:
        """沙箱 HOME 路径。"""

    @property
    def env(self) -> dict[str, str]:
        """构建好的环境变量字典，可直接传给 subprocess.Popen(env=...)。"""

    def __enter__(self) -> "IsolatedEnvironment": ...
    def __exit__(self, *exc) -> None: ...
```

### 2. `CredentialProvider` — 凭证注入策略

- [ ] 定义统一的凭证提供协议
- [ ] 支持多种凭证来源：文件复制、模板渲染、环境变量注入
- [ ] 凭证写入沙箱 HOME 的对应路径，而非真实 HOME

```python
class CredentialProvider(Protocol):
    """将凭证写入隔离环境的沙箱目录。"""
    def inject(self, env: IsolatedEnvironment) -> None: ...


class FileCredentialProvider:
    """从指定路径复制凭证文件到沙箱。"""

    def __init__(self, mappings: dict[str, Path]):
        """
        Args:
            mappings: {沙箱内相对路径: 源文件绝对路径}
                例: {".claude/settings.json": Path("/path/to/alt-settings.json")}
        """

    def inject(self, env: IsolatedEnvironment) -> None:
        # 将每个源文件复制到 env.home_dir / relative_path
        ...


class EnvVarCredentialProvider:
    """通过环境变量注入凭证（适用于读取 env 的 agent）。"""

    def __init__(self, variables: dict[str, str]):
        """
        Args:
            variables: {"ANTHROPIC_API_KEY": "sk-xxx", ...}
        """

    def inject(self, env: IsolatedEnvironment) -> None:
        # 将变量合并到 env.env_overrides
        ...


class CredentialSet:
    """组合多个 CredentialProvider，一次性注入。"""

    def __init__(self, providers: list[CredentialProvider]): ...
    def inject(self, env: IsolatedEnvironment) -> None: ...
```

### 3. `AgentRequest` 扩展

- [ ] 在 `AgentRequest` 中增加可选的隔离环境配置字段

```python
class AgentRequest(BaseModel):
    task_prompt: str
    workspace_dir: Path = Field(default_factory=Path.cwd)
    timeout: int = 900
    model: str | None = None
    allowed_tools: list[str] | None = None
    extra_args: dict[str, Any] | None = None

    # --- 新增 ---
    isolation: IsolationConfig | None = None  # 为 None 时保持现有行为


class IsolationConfig(BaseModel):
    """隔离环境配置。"""
    credential_files: dict[str, str] = {}    # 沙箱相对路径 -> 源文件路径
    env_overrides: dict[str, str] = {}       # 额外环境变量
    env_whitelist: list[str] | None = None   # 环境变量白名单
    env_blacklist: list[str] | None = None   # 环境变量黑名单
    persist_sandbox: bool = False            # 调试时保留沙箱
```

### 4. `BaseSubprocessAdapter` 改造

- [ ] `run()` / `run_async()` 中检测 `request.isolation`，有值时构建 `IsolatedEnvironment`
- [ ] 将 `env=None` 替换为 `env=isolated_env.env`
- [ ] 确保 `$HOME` 指向沙箱目录

```python
# base.py 中的关键改动（伪代码）
def run(self, request: AgentRequest) -> AgentResult:
    if request.isolation:
        with IsolatedEnvironment(...) as iso:
            for provider in self._build_providers(request.isolation):
                provider.inject(iso)
            return self._execute(request, env=iso.env, home=iso.home_dir)
    else:
        return self._execute(request, env=None, home=None)  # 保持向后兼容
```

### 5. YAML 配置支持

- [ ] 支持在配置文件中声明凭证 profile，按名称引用

```yaml
# clippet-config.yaml
credential_profiles:
  personal:
    files:
      ".claude/settings.json": "/Users/me/profiles/personal/claude-settings.json"
      ".claude/credentials.json": "/Users/me/profiles/personal/claude-creds.json"
    env:
      ANTHROPIC_API_KEY: "${PERSONAL_API_KEY}"

  work:
    files:
      ".claude/settings.json": "/Users/me/profiles/work/claude-settings.json"
    env:
      ANTHROPIC_API_KEY: "${WORK_API_KEY}"

  codex-test:
    files:
      ".codex/auth.json": "/Users/me/profiles/test/codex-auth.json"

adapters:
  - adapter_type: claude
    name: claude-personal
    options:
      credential_profile: personal

  - adapter_type: claude
    name: claude-work
    options:
      credential_profile: work
```

## 典型使用场景

### 场景 A：用不同 API key 并行运行同一 agent

```python
from clippet import ClippetRunner, AgentRequest, IsolationConfig
from clippet.adapters.claude import ClaudeAdapter

runner = ClippetRunner()
runner.register("claude", ClaudeAdapter())

results = runner.execute_parallel({
    "task-with-key-A": ("claude", AgentRequest(
        task_prompt="review this code",
        workspace_dir=Path("./repo"),
        isolation=IsolationConfig(
            env_overrides={"ANTHROPIC_API_KEY": "sk-key-A"},
        ),
    )),
    "task-with-key-B": ("claude", AgentRequest(
        task_prompt="review this code",
        workspace_dir=Path("./repo"),
        isolation=IsolationConfig(
            env_overrides={"ANTHROPIC_API_KEY": "sk-key-B"},
        ),
    )),
})
```

### 场景 B：替换 Claude Code 的 settings.json 进行测试

```python
request = AgentRequest(
    task_prompt="run the task",
    isolation=IsolationConfig(
        credential_files={
            ".claude/settings.json": "/tmp/test-settings.json",
            ".claude/credentials.json": "/tmp/test-creds.json",
        },
        persist_sandbox=True,  # 保留沙箱用于事后检查
    ),
)
```

### 场景 C：最小权限环境——仅暴露必要的环境变量

```python
request = AgentRequest(
    task_prompt="analyze code",
    isolation=IsolationConfig(
        env_whitelist=["PATH", "HOME", "ANTHROPIC_API_KEY"],
    ),
)
```

## 实现优先级

1. **P0** — `IsolatedEnvironment` 上下文管理器 + `$HOME` 覆盖
2. **P0** — `FileCredentialProvider`（文件复制方式注入凭证）
3. **P0** — `BaseSubprocessAdapter` 集成（`env` 参数传递）
4. **P1** — `IsolationConfig` 模型 + `AgentRequest` 扩展
5. **P1** — `EnvVarCredentialProvider` + 环境变量白名单/黑名单
6. **P2** — YAML credential profile 配置
7. **P2** — 沙箱清理策略（TTL / 手动 / persist 模式）

## 安全注意事项

- 凭证文件在沙箱中应设置 `0600` 权限
- `persist_sandbox=False` 时，退出 context manager 必须确保沙箱目录被删除（即使出现异常）
- 环境变量中的敏感值不应出现在日志或 `AgentResult.raw_output` 中
- 考虑未来增加 secret masking 能力，在 output parsing 阶段过滤已知的 key pattern
