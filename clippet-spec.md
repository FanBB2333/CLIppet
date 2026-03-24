# 🎭 Clippet Specification

## 1. 核心定位 (Overview)
**Clippet** 是一个专为封装、调度和监控异构命令行 AI Agent（CLI Agent）而设计的 Python 接口池与适配器框架。
它将极其分散、无状态且输出不可控的外部命令行工具（如 Claude Code, QoderCLI, Gemini CLI, Cline 等）统一成了强类型、结构化输出且副作用可控的 Python 对象。

该库作为 Benchmark 评测台的底层引擎，或更复杂的 Multi-Agent 系统中的执行节点，彻底摒弃了使用脆弱的正则表达式或大语言模型做二次输出来提取中间状态的“黑盒”测试法。

---

## 2. 核心特性 (Key Features)

- **统一接口协议 (Unified Protocol)**：无论是原生 Python 引擎直调，还是跨环境二进制包的 Subprocess 调用，对外暴露一致的 `Adapter.run(request)` 方法。
- **状态栏劫持与思维链提取 (Trajectory Extractor)**：突破传统 CLI 的黑盒限制，通过 IO 流劫持实时解析 Agent 的工具调用记录（Tools Use）和内部状态。
- **沙盒与断路器 (Sandbox & Circuit Breaker)**：内置针对工作区隔离的 Mock 集成支持，并提供硬超时（Hard Timeout）阻断功能。
- **声明式配置 (Declarative Configurations)**：通过 YAML/JSON 统一管理底层各个 CLI 的 Flag 与环境依赖。

---

## 3. 架构设计 (Architecture)

### 3.1 核心数据结构 (Data Models)
使用 `pydantic` 定义标准化输入输出：

```python
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class AgentRequest(BaseModel):
    task_prompt: str                          # 评测或调用的主任务描述
    workspace_dir: str                        # 目标代码库绝对路径
    timeout_seconds: int = 900                # 指令超时保护
    injected_skills: List[str] = Field(default_factory=list) # 外部注入的上下文 (如 PI/PUA skill)
    
class ToolCallRecord(BaseModel):
    tool_name: str
    parameters: Dict
    timestamp: float

class AgentResult(BaseModel):
    raw_output: str               # 最终回答的原始内容
    is_success: bool              # 执行是否异常中断
    execution_time: float         # 实际运行耗时
    
    # 结构化提取的数据，完全抛弃大模型二次提取
    tool_calls: List[ToolCallRecord]
    steps_taken: int
    error_message: Optional[str] = None
```

### 3.2 接口抽象层 (The Protocol)
通过 Python `Protocol` 约束适配器行为：

```python
from typing import Protocol

class ClippetAdapter(Protocol):
    """所有 Agent CLI 需要实现的公共协议"""
    
    @property
    def agent_name(self) -> str: ...
    
    def run(self, request: AgentRequest) -> AgentResult: ...
    
    async def run_async(self, request: AgentRequest) -> AgentResult: ...
```

### 3.3 预置适配器列表 (Built-in Adapters)
框架建议分为两类适配器实现：
1. **`NativeAdapter` (原生适配层)**：对于 Python 开发的工具（如 `QoderCLI`），直接 Import 其核心库内存交互，零启动延迟且精准捕获调用栈。
2. **`SubprocessAdapter` (子进程适配层)**：对于像 `Claude Code` 或 `Cline` 这样跨语言甚至闭源工具，使用伪终端（PTY）模式拦截其输出，配合专用的正则解析器将特定输出形态流式转换为结构化的 Python 对象。

---

## 4. 使用示例 (Usage Example)

```python
from clippet import AgentRequest
from clippet.adapters import ClaudeSubprocessAdapter, QoderNativeAdapter
from clippet.orchestrator import ClippetRunner

# 初始化统一调度器
runner = ClippetRunner()
runner.register("claude-sonnet", ClaudeSubprocessAdapter(model="sonnet"))
runner.register("qoder-lite", QoderNativeAdapter(model="lite"))

# 构造标准请求对象
req = AgentRequest(
    task_prompt="Find and fix the memory leak in src/server.py",
    workspace_dir="/Users/l1ght/repos/my-project"
)

# 统一执行并获取强类型的返回 (支持并行调用多个)
result = runner.execute("claude-sonnet", req)

print(f"Time taken: {result.execution_time}s")
print(f"Status Success: {result.is_success}")
print(f"Tools used: {[t.tool_name for t in result.tool_calls]}")
```

---

## 5. 预期项目结构图 (Directory Structure)

```text
clippet/
├── __init__.py
├── models.py           # 数据通信对象 (AgentRequest, AgentResult 等)
├── protocols.py        # 接口定义 (ClippetAdapter)
├── config/
│   └── registries.yaml # 用来注册外部 CLI 二进制文件的默认配置
├── adapters/           
│   ├── base.py         # 共享基类
│   ├── subprocess.py   # 子进程通用处理基类 
│   ├── claude.py       
│   ├── gemini.py       
│   └── qoder.py
├── parsers/            # 专门从各 CLI 大段 stdout 流中榨取 Tool/Step json 的流解析器
│   └── regex_extractor.py 
└── orchestrator.py     # 外部暴露的多 Agent 并发调度入口
```
