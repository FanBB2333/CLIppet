# Codex 桌面 App 多实例、多账户调研

调研日期：2026-11-02。范围：当前 CLIppet 工作区、这台 Mac 上安装的桌面 App，以及 OpenAI 官方文档。本次增加调研文档，不实现桌面启动功能。

> **2026-11-02 更新（实现）**：CLIppet 已按第 5 节实现 macOS 启动器：`clippet -e NAME codex app [PATH]`，TUI 中按 `g`（可对多个已标记环境同时启动），代码见 `clippet/desktop.py`，用法见 README §3.7。同一环境已有实例在运行时只把它切到前台，不再起第二个进程，此时也不同步链接账户的登录。之后复查本机安装：`codesign --verify --deep --strict /Applications/ChatGPT.app` 已通过，签名团队为 OpenAI（`2DC432GLL2`），下文第 6 节记录的签名失败已不再出现。第 6 节末尾列出的真实双账户 GUI 验收仍未进行。

## 结论

**有明确的实现机会。** 本机桌面 App 的启动代码已经包含独立桌面数据目录、独立 Codex 数据目录及新实例启动的机制。CLIppet 可以为每个持久环境维护一组目录，启动同一个已安装 App 的不同进程，使系统默认 App 和各环境分别使用自己的登录状态。

但是，**当前 `clippet -e NAME codex app .` 只是透传，不具备完整的桌面隔离逻辑**。不能把当前 CLI 的多账户能力直接等同于桌面 App 已经支持多账户并行。

本次已完成源码和安装包静态核查、CLIppet 模拟启动，以及两个真实后台进程的本地测试凭据读取验证。**尚未完成两个真实 ChatGPT 账户分别登录两个 GUI 实例的端到端验收**。本机标准 `codex app` 启动还遇到签名校验失败，详见验证记录。

## 1. 调研对象

本机安装的 App 已命名为 `ChatGPT.app`，其内部仍使用 Codex 的标识：

| 项目 | 实际值 |
| --- | --- |
| App 路径 | `/Applications/ChatGPT.app` |
| Bundle ID | `com.openai.codex` |
| 主程序 | `Contents/MacOS/ChatGPT` |
| App 版本 | `26.1027.51404`，构建 `23880` |
| Electron 包 | `Contents/Resources/app.asar` |
| 包内名称 | `openai-codex-electron` |
| JavaScript 入口 | `.vite/build/early-bootstrap.js` |
| 内置 Codex CLI | `Contents/Resources/codex-cli/bin/codex` |
| 内置 CLI 版本 | `0.166.0-alpha.5.1` |

因此，此报告中的“Codex 桌面 App”指这台机器上实际安装的上述 App。启动器不应硬编码只查找 `/Applications/Codex.app`。

## 2. 当前 CLIppet 如何启动 Codex

启动链路是：

```text
选择环境或凭据文件
  → 解析适配器及 IsolationConfig
  → build_interactive_command() 拼接 codex 命令
  → 创建持久 HOME 或临时 HOME，注入凭据
  → subprocess.run(..., env=isolated.env)
  → 子命令返回后同步凭据、清理临时 HOME
```

关键实现位置：

| 文件 | 当前职责 |
| --- | --- |
| `clippet/adapters/codex.py`，`build_interactive_command()` | 将包括 `app` 在内的附加参数原样拼接到 `codex` 后面 |
| `clippet/cli.py`，`_run_interactive_command()` | 等待命令返回，随后同步凭据并调用 `isolated.cleanup()` |
| `clippet/isolation.py`，`_build_env()` | 设置隔离 HOME，清洗身份相关环境变量 |
| `clippet/isolation.py`，`cleanup()` | 删除临时 HOME；保留持久 HOME |
| `clippet/config/detector.py`，`create_adapter_with_second_home()` | 复用持久环境；发现 `auth.json` 时给 CLI 添加文件凭据存储覆盖参数 |
| `clippet/agents.py`，Codex 的 `identity_env` | 清洗继承的 `CODEX_HOME`、部分 `CODEX_*` 和 `OPENAI_*` 身份变量 |

对 CLI 来说，这个流程成立：进程使用环境内的 HOME，凭据、会话等也落在该目录。GUI 启动多了一层桌面进程，启动命令的返回时间也不等于 GUI 的退出时间。

实际模拟 `clippet -c <测试凭据> codex app <项目路径>`，拦截 `subprocess.run` 而不启动 GUI，得到：

| 检查项 | 结果 |
| --- | --- |
| `app` 参数 | 确实被透传给 `codex` |
| `HOME` | 指向 CLIppet 临时目录 |
| `CODEX_HOME` | 被清洗，没有重新显式指定 |
| 父进程的 `CODEX_ELECTRON_USER_DATA_PATH` | 被继承，未被当前 Codex 身份变量表清洗 |
| 启动命令返回后的临时 HOME | 已删除 |

这证明当前透传路径存在三个缺口：没有主动隔离桌面数据目录、没有固定桌面进程的 Codex home、没有按 GUI 生命周期管理临时目录。

## 3. 桌面 App 实际如何决定数据目录

### 3.1 桌面数据与 Codex 数据是两套路径

安装包中可见以下逻辑，以下为便于阅读的等价伪代码：

```javascript
desktopUserData = env.CODEX_ELECTRON_USER_DATA_PATH?.trim()
  ? resolve(env.CODEX_ELECTRON_USER_DATA_PATH)
  : defaultDesktopDataDirectory;
app.setPath("userData", desktopUserData);

codexHome = process.env.CODEX_HOME ?? join(os.homedir(), ".codex");
```

`CODEX_ELECTRON_USER_DATA_PATH` 控制 Electron 的 `userData` 路径；`CODEX_HOME` 控制 Codex 后端读取的配置与凭据等数据。需要同时设置二者，且每个账户使用不同路径。

仅修改 HOME 不足以保证 Electron 的平台数据目录随之改变；仅指定 `--user-data-dir` 也不足以固定 Codex 后端的账户目录。当前包还会主动调用 `app.setPath("userData", ...)`，因此应优先使用包内实际识别的环境变量，并保持 Chromium 参数与之相同。

### 3.2 App 会重新读取 shell 环境

启动过程中，App 会加载交互 shell 的环境并合并回 `process.env`。这里有一条非常关键的保护逻辑：

- 若启动时显式提供了 `CODEX_ELECTRON_USER_DATA_PATH`，会先保存当时的 `CODEX_HOME`。
- shell 环境加载完成后，再恢复这个已保存的 `CODEX_HOME`。

因此，同时设置这两个变量还有第二个作用：避免 shell 初始化文件中的 `CODEX_HOME` 把独立实例重新指回系统默认账户。

### 3.3 独立 profile 会影响单实例逻辑

App 先设置 `userData`，再决定是否调用 `requestSingleInstanceLock()`。包内判断函数的等价逻辑是：

```javascript
useSingleInstanceLock = isPackaged && (!isMacOS || hasExplicitUserDataPath);
```

也就是说，这个版本在 macOS 打包模式下，明确考虑了“显式提供独立桌面数据目录”的启动方式。结合设置目录和申请锁的顺序，可以推断不同 profile 有实现独立实例的基础；同一 profile 的重复打开应作为同一个实例管理。实际 GUI 锁行为仍需端到端验收。

### 3.4 包内已有相同形态的新实例启动代码

桌面 App 自己的内部 demo 启动功能使用了以下参数组合：

```text
/usr/bin/open -n
  --env CODEX_HOME=<demo Codex 目录>
  --env CODEX_ELECTRON_USER_DATA_PATH=<demo 桌面目录>
  <App 路径>
  --args --user-data-dir=<同一个 demo 桌面目录>
```

这是本机安装包内的直接依据，说明该参数组合确实被 App 自身用于独立启动场景。它是内部实现，不能视为公开承诺长期稳定的多账户产品接口。

## 4. 登录账户如何传递到后端

本地桌面连接会启动内置 `codex app-server`，使用包含 `process.env` 的环境，并通过 `account/read`、`account/login/start` 等请求管理账户。本地 stdio 路径中，显式 `CODEX_HOME` 会传给后端。

包内也存在有条件连接本地 daemon 的路径，其控制 socket 路径由 Codex home 拼出：

```text
<CODEX_HOME>/app-server-control/app-server-control.sock
```

当前 IPC 的首选 socket 同样位于 `<CODEX_HOME>/ipc/ipc.sock`。但代码还存在系统临时目录中的旧 IPC endpoint 回退，因此不能把两组目录隔离解释成操作系统级完全隔离。旧 IPC 回退和窗口路由应纳入 GUI 验收。

OpenAI 官方文档确认，文件凭据存储使用 `CODEX_HOME/auth.json`，并由 `cli_auth_credentials_store` 控制。建议独立 profile 使用：

```toml
cli_auth_credentials_store = "file"
```

参见 [OpenAI：Authentication](https://learn.chatgpt.com/docs/auth#credential-storage)。

**这项配置应持久写入环境内的 `config.toml`。** 不能仅依赖 CLIppet 给外层 `codex` 添加的 `-c` 参数，再假定它会自动传递给另一个 GUI 进程。本机包还包含受功能开关控制的 `secret_auth_storage` 路径；正式验收应确认当前发布渠道实际选择的凭据存储方式。

## 5. 建议在 CLIppet 中怎么落地

建议保留现有持久环境模型，增加专用桌面启动器：

```text
系统默认 App
  ├── 默认 Electron userData
  └── 默认 ~/.codex

CLIppet 环境 acct-a
  ├── <env-a>/.codex-desktop
  └── <env-a>/.codex → 账户 A

CLIppet 环境 acct-b
  ├── <env-b>/.codex-desktop
  └── <env-b>/.codex → 账户 B
```

无需复制、修改 App bundle，也不需要修改系统默认账户文件。核心是同一份 App 程序、多个进程、每个进程一组稳定目录。

可将 `clippet -e acct-a codex app .` 设计为桌面专用分支。注意：这是建议新增的行为，当前版本仍只是透传。启动器应完成：

1. 解析并验证持久环境，检查该环境是否已有桌面实例；已运行时向该实例打开项目，避免先覆盖运行中实例的凭据。
2. 为该环境设置绝对路径 `CODEX_HOME` 与 `CODEX_ELECTRON_USER_DATA_PATH`，并准备目录和持久配置。
3. 处理继承的桌面路径、CLI 运行时覆盖、后端连接及身份变量；不要沿用父实例指向另一 profile 的配置。
4. 使用 macOS 的新实例启动方式，明确向新进程传入上述变量；可选直接启动 bundle 内主程序以获得更直接的进程管理，但需要另验 Dock 和项目打开行为。
5. 持久保存账户、会话及 GUI 状态；启动命令返回后保留这些目录。
6. 对链接账户沿用现有同步策略，同时避免在 GUI 运行时重复写入凭据。长时间并行使用优先分别正常登录，避免复制同一组 OAuth refresh token 后独立刷新。

第一版建议只支持 `-e` 持久环境。`-c` 的临时凭据模式应先转换为持久环境，或由专门的 GUI 生命周期管理器负责保留及退出后的同步，不能套用现有“子命令返回即清理”的逻辑。

下面展示的是后续启动器的参数形态，**本次没有执行这条 GUI 启动命令，也不是已完成验收的操作步骤**：

```bash
env_home="$(clippet env path acct-a)"

/usr/bin/open -n \
  --env "CODEX_HOME=$env_home/.codex" \
  --env "CODEX_ELECTRON_USER_DATA_PATH=$env_home/.codex-desktop" \
  -a /Applications/ChatGPT.app \
  /Users/l1ght/repos/CLIppet \
  --args "--user-data-dir=$env_home/.codex-desktop"
```

这里 `open -n` 的作用是请求新进程；两个目录变量决定 profile。只多开窗口或只使用 `open -n`，都没有为不同账户准备完整隔离条件。

## 6. 实际验证记录与边界

| 验证 | 结果 | 能证明什么 |
| --- | --- | --- |
| CLIppet 启动代码阅读 | 完成 | 当前为 CLI 进程隔离，未设置专用桌面 profile |
| 已安装 App 的 plist、package.json、入口及 chunks 阅读 | 完成 | 当前版本存在上述路径、shell 恢复、锁及 demo 启动机制 |
| CLIppet 透传启动模拟 | 完成 | 继承桌面目录、缺少显式 Codex home、临时 HOME 随命令结束而删除 |
| 两个并行真实 app-server，本地合成 API key，`getAuthStatus` | 成功 | 两个进程分别返回各自 profile 的测试 key，没有读到另一份测试 key |
| 合成 ChatGPT JWT，`account/read` | 未成功 | 返回 `workspace routing discovery failed`；合成凭据不能验证真实 ChatGPT 登录 |
| 内置 CLI 的 `app --help` | 完成 | 公开参数有工作区路径及安装下载地址，没有提供桌面账户/profile 选择参数 |
| 标准 `codex app <项目路径>` 启动探测 | 在打开前失败 | CLI 拒绝已安装 App 的签名，未捕获到后续 `open` 调用 |
| `/usr/bin/codesign --verify --deep --strict` | 同样失败 | 独立复核了当前安装的签名问题 |
| 两个 GUI 实例真实登录不同 ChatGPT 账户 | 未进行 | 不能声称桌面多账户已经实测通过 |

本次后台验证使用项目中的合成凭据函数和临时目录，没有读取真实 `auth.json`，没有发起模型推理。代理指向本机未使用端口，以避免这项本地读取验证依赖外部认证服务。

签名错误原文：

```text
Desktop app at /Applications/ChatGPT.app failed OpenAI signature verification
(team 2DC432GLL2, bundle com.openai.codex):
/Applications/ChatGPT.app: invalid signature (code or signature have been modified)
In architecture: arm64
```

这只能说明当前安装未通过检查，不能仅凭错误判断是谁修改了什么。静态分析的结果也因此只能直接归属于本机这份安装，不能直接推广为签名有效的官方发行包的行为保证。后续 GUI 验收前应通过正常安装/更新取得签名有效的官方 App，并复核上述内部开关。本次没有修改签名或绕过校验。

GUI 验收还需要验证：系统默认账户保持不变、A/B 分别显示预期用户及 workspace、退出 A 不影响 B、重开后仍保留各自身份、同一 profile 重开会回到正确实例、项目打开和登录回调不会被另一个实例接收。若要求 CLI 与 GUI 同时使用同一 profile，还应专门验证凭据刷新和后台连接共存。

## 7. 可复核依据

官方文档：

- [Codex / ChatGPT CLI reference：`codex app`](https://learn.chatgpt.com/docs/cli/reference#codex-app)：说明桌面启动命令及其公开参数。没有在该节找到承诺独立多账户实例的公开接口。
- [Authentication：Credential storage](https://learn.chatgpt.com/docs/auth#credential-storage)：说明文件存储、系统凭据存储及 `CODEX_HOME/auth.json`。

本机静态证据均来自 `/Applications/ChatGPT.app/Contents/Resources/app.asar`，不是官方文档对产品能力的保证。表中位置为解码后 JavaScript 的字符偏移，便于复核压缩文件：

| 包内文件 | 证据位置 |
| --- | --- |
| `.vite/build/early-bootstrap-339-9edb60e5d8b0547e.js` | 约 `131987`：设置 Electron userData；`132517`：请求单实例锁；`150702`：锁判断函数 |
| `.vite/build/early-bootstrap-133-a2b781596743bab7.js` | 约 `30469`：保存显式 CODEX_HOME；`31752`：shell 环境合并后恢复 |
| `.vite/build/early-bootstrap-439-a0f25ba32c465de1.js` | 约 `1738737`：内部 demo 使用 `open -n` 和两组目录；`641038` 起：登录 RPC |
| `.vite/build/early-bootstrap-824-3c24dda6dba622ec.js` | 约 `157996`：后端 daemon socket；`159000` 起：本地后端环境；`199938`：Codex home 解析；`205630`：IPC socket |
| `.vite/build/early-bootstrap-339-9edb60e5d8b0547e.js` | 约 `1119576`：IPC router 对旧 endpoint 的回退 |

上述版本包含可行机制，且后端目录隔离已做局部运行验证。因此值得在 CLIppet 中实现一个 macOS 桌面启动器；是否能标记为正式支持，应以签名有效的官方版本上的真实双账户 GUI 验收为准。Windows、Linux 未在本次调研中实测。
