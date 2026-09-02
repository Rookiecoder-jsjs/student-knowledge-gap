# Codex 魔改工程架构文档 v0

## 0. 锚定信息与声明

| 项 | 值 |
| --- | --- |
| 上游仓库 | openai/codex |
| 参考克隆目录 | `codex/`（本仓库下的只读参考克隆） |
| 锁定 tag | `rust-v0.149.1` |
| 锁定 commit | `ff29a44391deccde0aba0f8390337d7f3c319ea4` |
| 文档日期 | 2026-08-24 |
| license | Apache-2.0 |

> **本文所有路径断言以锚点为准。** 文中所引用的每一个相对路径，均以上述 commit 的 `codex/` 克隆为基准（路径形如 `codex-rs/core/src/codex_thread.rs`，即 `codex/codex-rs/...`）。若你在其他版本上核对，行号/函数签名可能偏移；路径如果失效，以锚点 commit 回查。

**写给谁看**：你是一个 Python / Vue 主栈的工程师，第一次系统接触这个 Rust workspace。本文不写"上游贡献者怎么做"，而是给你一张可导航的地图 + 三条关键数据流的精确路径。最终目标是：你能在 `codex-rs/` 里指认"改哪一层、动哪个文件"。

---

## 1. Workspace 总览

workspace 根：`codex-rs/`。成员清单的**唯一权威来源**是 `codex-rs/Cargo.toml` 的 `[workspace.members]`，锚点 133 个成员，Phase 1 裁剪后为 **118** 个，加上 §6.3 首笔新增 `school-authz` 现为 **119** 个（含 `core`、`protocol`、`app-server` 等主力 crate，以及大量 `utils/*` 工具 crate；裁剪账本见 [CRATES.md](CRATES.md)，新增见本文件 §1.4.1 与 [CRATES.md](CRATES.md) 预定新增）。`resolver = "2"`，统一 `edition = "2024"`，`version = "0.149.1"`。

下面**按职能分组**（分组是我们为导航方便追加的，非上游定义；清单本身来自 members）：

### 1.1 协议 / 数据类型层

| crate（目录） | 一句话职责 |
| --- | --- |
| `protocol` | 全 workspace 的核心消息定义：`Op`（提交操作）、`EventMsg`（事件）、`AskForApproval`、`AskForApproval` 相关审批类型、`TurnInput*`、`items`、`models`、`permissions`。这是"协议"层，最重的文件是 `protocol/src/protocol.rs`（约 6000 行）。 |
| `core-api` | Core 对外暴露的 API 类型（与 `app-server-protocol` 区分的核心侧类型）。 |
| `model-provider-info` | 模型商 / provider 元信息（如 `OPENAI_PROVIDER_ID`）。 |
| `models-manager` | 模型列表与默认模型管理器（`SharedModelsManager`）。 |
| `model-provider` | ModelProvider 工厂（`create_model_provider`），按 provider id 创建客户端。 |
| `codex-backend-openapi-models` | 后端 OpenAPI 生成的模型类型。 |
| `response-debug-context` | 响应调试上下文。 |
| `codex-api` / `codex-client` | 外部 API / 上游 client 相关类型。 |

### 1.2 核心循环（大脑）

| crate（目录） | 一句话职责 |
| --- | --- |
| `core` | **最重要的 crate**。`Session` / `SessionIo`、`submission_loop`、`turn` 循环、`ThreadManager`、工具注册表/路由、MCP 管理器、execpolicy、沙箱判定等全在这里。 |
| `codex-thread-store`（`thread-store`） | `ThreadStore` 抽象 + `LocalThreadStore` + `LiveThread`，负责 rollout 的"持久化策略"与元数据同步。 |
| `codex-history` | 历史封装：`InitialHistory` / `ResumedHistory` / `RolloutItem` / `RolloutLine`。 |
| `rollout` | 会话 JSONL 落盘与发现：`RolloutRecorder`（append-only 写入）、`state_db`（SQLite 镜像）、`compression`、`list`、`search`。 |
| `codex-rollout-trace`（`rollout-trace`） | rollout 追踪：`ThreadTraceContext`、`AgentResultTracePayload` 等。 |
| `otel`（`codex-otel`） | 遥测：`SessionTelemetry`、OTel 指标、trace 上下文工具。 |
| `codex-model-provider`（`model-provider`） | 模型 provider 创建。 |
| `codex-features`（`features`） | 特性开关集合（`FEATURES`、`Feature`）。 |
| `codex-connectors`（`connectors`） | 外部连接器（app）元数据与匹配。 |
| `codex-core-plugins`（`core-plugins`） | 插件管理器（`PluginsManager`），插件命令归属、推荐候选。 |

### 1.3 工具执行 / 沙箱

| crate（目录） | 一句话职责 |
| --- | --- |
| `tools` | 工具类型本身（`ToolName`、`ToolSpec`、`DiscoverableTool`）。 |
| `core` 内 `src/tools/` | 工具运行期：`registry`（注册 / 冲突）、`router`（`ToolRouter`）、`parallel`（`ToolCallRuntime`）、`orchestrator`、`handlers/*`、`approvals`。 |
| `exec` / `exec-server` / `exec-server-protocol` | 统一执行进程 / 执行服务器及其协议（`unified_exec`、`EnvironmentManager`）。 |
| `execpolicy` | 执行策略规则（`Policy`、`RequirementsExecPolicy`、前缀规则迁移）。 |
| `sandboxing` | 沙箱策略类型与转换（`policy_transforms`、`intersect_permission_profiles`）。 |
| `process-hardening` | 进程加固（结构/常量）。 |
| `file-system` | 文件系统策略。 |
| `shell-command` / `shell-escalation` | shell 命令解析 / 权限提升（macOS `ESCALATE_SOCKET_ENV_VAR`）。 |
| `code-mode` / `code-mode-host` / `code-mode-protocol` / `code-mode-runtime` | code-mode 会话、host 传输与运行期。 |
| `v8-poc` | V8 引擎 PoC（调用 `v8` crate）。 |
| `apply-patch` | 补丁应用（`apply_patch`）。 |

### 1.4 应用服务层（app-server）

| crate（目录） | 一句话职责 |
| --- | --- |
| `app-server` | **最关键的对外服务**：JSON-RPC 入口、请求处理器（`request_processors/*`）、连接管理与出站路由。 |
| `app-server-protocol` | 服务器的 JSON-RPC 协议类型：`ServerRequest` / `AppServerNotification` 枚举、`v1`/`v2` 结构、`RequestId`、`rpc.rs`。 |
| `app-server-transport` | 传输层：`AppServerTransport` 枚举、stdio / unix-socket / websocket 启动器、认证（`auth.rs`）、远端控制。 |
| `app-server-client` | app-server 客户端。 |
| `app-server-daemon` | daemon 化启动。 |
| `app-server-test-client` | 测试用客户端。 |
| `app-server-protocol-noop-macros` | noop 宏。 |
| `app-server-transport` 的 `remote_control/` | 远端控制（配对、客户端列表）。 |

### 1.4.1 §6.3 新增（fork 自有，非上游成员）

| crate（目录） | 一句话职责 |
| --- | --- |
| `school-authz` | 教师↔班级鉴权原语：签名 token 校验（HMAC-SHA256，与 sc 后端 `backend/app/auth.py` 同格式同密钥）+ `assert_class_access` 权限断言 + `school-authz-mcp` stdio shim。首个官方壳做不到的能力（§6.3 / DELTA D-034）。**部署形态已退役（装车批第 5 批 / D-035）**：sc MCP 迁 backend 进程后身份改逐请求 token 校验（`backend/app/mcp_http.py`），crate 保留作参考实现；差异能力以「backend 逐请求教师授权」形态延续 |

### 1.5 持久化 / 状态

| crate（目录） | 一句话职责 |
| --- | --- |
| `state` | SQLite 状态运行期（`StateRuntime`、`SqliteConfig`、`ThreadMetadata`、目标/队列存储），是 `state_db` 的实现体。 |
| `rollout` | 见上：JSONL 主存储 + `state_db` 镜像。 |
| `thread-store` | 线程存储抽象（`ThreadStore` trait、`LocalThreadStore`、`InMemoryThreadStore`）。 |
| `secrets` | 密钥存储（keyring）。 |
| `keyring-store` | Keyring 实现。 |
| `login` | 登录/认证（`AuthManager`、`CodexAuth`）。 |
| `aws-auth` / `workload-identity` | AWS 认证 / workload identity。 |
| `network-proxy`（`responses-api-proxy`） | 网络代理（`NetworkProxy`）/ responses API 代理。 |
| `cloud-config` / `cloud-tasks` / `cloud-tasks-client` / `cloud-tasks-mock-client` | 云端配置与任务队列（及其客户端/mock）。  |
| `backend-client` | 后端客户端。 |

### 1.6 前端 / 界面 / CLI

| crate（目录） | 一句话职责 |
| --- | --- |
| `tui` | 终端界面（`ratatui`）。 |
| `cli` | 命令行入口（`clap`），最终二进制。 |
| `codex-home` | codex home 目录解析。 |
| `config` | 配置加载（`config.toml`、`mcp_servers`、层叠 `ConfigLayerStack`、`ConfigManager`）。 |
| `feedback` / `diagnostics` | 反馈回传 / 诊断指标。 |
| `terminal-detection` | 终端检测（`user_agent`）。 |
| `install-context` / `build-info` / `history`（`codex-history`） | 安装上下文 / 构建信息 / 历史。 |

### 1.7 沙箱安全（单独列出）

| crate（目录） | 一句话职责 |
| --- | --- |
| `process-hardening` | 进程加固。 |
| `sandboxing` | 沙箱策略类型与权限画像（`PermissionProfile`、`SandboxEnforcement`）。 |
| `execpolicy` | 执行策略规则引擎。 |
| `shell-escalation` | shell 权限提升（macOS）。 |

### 1.8 基础设施 / 工具 crate

| crate（目录） | 一句话职责 |
| --- | --- |
| `async-utils` | 异步工具（`CancellationToken` 助手、`or_cancel`）。 |
| `ansi-escape` | ANSI 转义处理。 |
| `arg0` | 进程 `argv[0]` 处理。 |
| `http-client` | HTTP 客户端封装。 |
| `otel` | 遥测/追踪。 |
| `stdio-to-uds` | stdio → UDS 桥。 |
| `uds` | Unix Domain Socket 工具。 |
| `websocket-client` | WebSocket 客户端。 |
| `rmcp-client` | **MCP 客户端**（`rmcp` 的封装）：stdin/stdio 启动、streamable-http、OAuth、in-process 传输。 |
| `codex-mcp` | MCP 绑定层：`McpBinding`、`McpConfig`、`McpRuntime`、`connection_manager`、`tools`。 |
| `mcp-server` | 内嵌 MCP 服务器。 |
| `prompts` | 提示词模板（`codex-prompts`）。 |
| `skills` / `skill` / `host-skills` | 技能系统。 |
| `hooks` | Hook 运行期。 |
| `diagnostics` | 诊断服务。 |
| `utils/*` | 大量工具 crate（`absolute-path`、`cache`、`path-uri`、`cargo-bin`、`git-utils`、`pty`、`readiness`、`rustls-provider`、`sleep-inhibitor`、`approval-presets`、`stream-parser`、`template`、`fuzzy-match`、`output-truncation`、`json-to-toml`、`home-dir`、`elapsed`、`sandbox-summary`、`oss`、`string`、`cli` 等）。 |
| `plugin` | 插件核心类型。 |
| `model-provider-info` / `models-manager` | 模型信息与模型管理。 |

> 注：`codex-cli`（仓库根目录的一个独立目录，非 Cargo member）是 JS/TS 侧的 CLI，不在本 workspace 的 Rust crate 清单里，本文不展开。

---

## 2. 三条关键数据流

> 以下每个断点都会给出**入口到出口**的精确文件行号引用。行号以锚点 commit 为准。

### 2.1 数据流① 提交 → Turn 循环 → 工具分派 → 事件流

**核心命题**：一次用户提交，从 `Op` 进入 `Submission` 队列（SQ），被 `submission_loop` 取出，判成"开始/续接一个 Turn"；Turn 内模型的工具调用经 `registry`/`router` 分派到 handler；handler 产生的 `EventMsg` 进事件队列（EQ），推给订阅方。

**分段路径**：

**A. 提交入口（SQ）**
- 类型：`SessionIo`（`codex-rs/core/src/session/mod.rs` 第 365 行）持有：
  - `tx_sub: Sender<Submission>` —— 提交口（**bounded**，容量 `SUBMISSION_CHANNEL_CAPACITY = 512`，见第 458 行）。
  - `rx_event: Receiver<Event>` —— 事件出口（**unbounded**，见第 531 行 `async_channel::unbounded()`）。
  - `agent_status: watch::Receiver<AgentStatus>`、`session_loop_termination`。
- 提交函数：
  - `SessionIo::submit(&self, op)`（第 799 行）→ `submit_with_trace(...)`（第 806 行）→ `new_submission_id()` 生成 `uuid7` 作为 `Submission.id` → `submit_with_id`（第 826 行）`tx_sub.send(sub)`。
  - `SessionIo::submit_turn_input(&self, request, mode)`（第 841 行）把 `Op::TurnInput { request, mode, reply }` 塞进队列，并 `await reply_rx` 直到 Core 给出**路由决策**（`TurnInputSubmission`）。注意它只等"决策"，**不**等整轮 Turn 结束。
- `Submission` / `Op` 定义：`codex-rs/protocol/src/protocol.rs`，`Op` 枚举在第 **543–698** 行（含 `Interrupt`、`TurnInput`、`ExecApproval`、`PatchApproval`、`RecoverTurn`、`Shutdown` 等；`#[non_exhaustive]`）。

**B. 从队列取 Op → 变成 Session/Turn/Task**
- 消费端：`submission_loop(sess, config, rx_sub)`（`codex-rs/core/src/session/handlers.rs` 第 515 行）。`while let Ok(sub) = rx_sub.recv().await`，用 `submission_dispatch_span(&sub)` 记录，然后 `match sub.op` 分派（第 526–672 行）。
- `Op::TurnInput { request, mode, reply }` → `turn_input::handle(&sess, *request, mode, sub.id)`（第 570–578 行，`codex-rs/core/src/session/turn_input.rs` 第 **141** 行）。
- `turn_input::handle` 按 `mode` 走三条路：
  - `StartOrSteer` → `start_or_steer`（第 167 行）：先试 `steer_input`（若已有活动 Turn 且类型是 `Regular` 则"续接"），否则 `apply_started` 创建 `TurnContext` 并 `session.spawn_task(...)`。
  - `StartIfIdle` → `start_if_idle`（第 252 行）：**若 `active_turn` 里已有任务则返回 `NotSubmitted::NotIdle`**（这是"单任务"的判定点，见下）。
  - `Steer { expected_turn_id }` → `steer`（第 374 行）。
- 关键判定：`Session::steer_input`（第 478 行）读 `self.active_turn`；若 `active_turn.task` 为 `None` 返回 `NotSubmittedReason::NoActiveTurn`；若 `task.kind` 不是 `Regular`（是 `Review`/`Compact`）返回 `NotSubmittedReason::ActiveTurnNotSteerable`。
- Task 启动：`Session::spawn_task(turn_context, task_input, RegularTask::new())`（第 242 行）。

**C. Turn 循环**
- `RegularTask::run`（`codex-rs/core/src/tasks/regular.rs` 第 39 行）先发 `EventMsg::TurnStarted`，然后 `loop { run_turn(...) }`，只要 `input_queue.has_pending_input(...)` 就继续，否则返回 `last_agent_message`。
- `run_turn(...)`（`codex-rs/core/src/session/turn.rs` 第 **153** 行）：
  - 预处理（`run_pre_sampling_compact`、`required_mcp_servers_for_input`、`capture_step_context`、`build_skills_and_plugins`、`run_hooks_and_record_inputs`）。
  - 进入 `loop`：构造 `sampling_request_input` → `run_sampling_request(...)`（第 383 行调用）。
- `run_sampling_request(...)`（第 **1342** 行）构造 `ToolCallRuntime`（第 1355 行）并进入重试 `loop`：构造 `Prompt`（`build_prompt`，第 1314 行，取 `step_context.tool_router.model_visible_specs()`）→ `try_run_sampling_request(...)`。
- `try_run_sampling_request(...)`（第 **2181** 行）：`client_session.stream(prompt, ...)` 得到 `ResponseStream`，`while let Some(event) = stream.next()` 逐个处理 `ResponseEvent`；对 `OutputItemDone` 调用 `handle_output_item_done(...)`（第 2386 行），**其返回值里的 `output_result.tool_future` 被 `in_flight.push_back(tool_future)`（第 2393–2394 行）排队**，并由 `FuturesOrdered` 并行执行。

**D. 工具分派（registry / router）**
- `ToolCallRuntime::handle_tool_call`（`codex-rs/core/src/tools/parallel.rs` 第 **73** 行）→ `handle_tool_call_with_source`（第 92 行）：
  - `router.tool_supports_parallel(&call)`、`router.tool_runtime(&call)`、`router.tool_waits_for_runtime_cancellation(&call)`，
  - 用一个 `parallel_execution: Arc<RwLock<()>>` 做"是否允许并行"的门：`supports_parallel` 用 `read()`，否则 `write()`（第 153–157 行），
  - 调 `router.dispatch_tool_call_with_terminal_outcome(...)`（第 164 行）。
- `ToolRouter::dispatch_tool_call_with_terminal_outcome`（`codex-rs/core/src/tools/router.rs` 第 **233** 行）→ `dispatch_tool_call_with_code_mode_result_inner`（第 256 行），构造 `ToolInvocation` → `registry.dispatch_any_with_terminal_outcome(invocation, ...)`。
- `ToolRegistry::dispatch_any_with_terminal_outcome`（`codex-rs/core/src/tools/registry.rs` 第 **488** 行）：按 `invocation.tool_name.with_default_namespace()` 查 `tools` map → 取 `RegisteredTool.runtime` → `dispatch`。注册/冲突逻辑在 `register_trusted_with_exposure`（第 313 行，trusted 冲突会 `error_or_panic`）和 `register_external_with_exposure`（第 347 行，external 冲突**跳过**并 `record_collision`）。
- 工具注册来源：`build_tool_router(...)`（`codex-rs/core/src/tools/spec_plan.rs` 第 **121** 行）——内建工具 `add`/`register_trusted`，MCP 工具经 `mcp_handler_cache.append_mcp_tools(...)` 后 `apply_mcp_tool_exposure_policy(...)`（第 178 行）决定其暴露度。

**E. 事件进 EQ 推给订阅方**
- 工具/模型输出最终都会走 `Session::send_event`（`codex-rs/core/src/session/mod.rs` 第 **1913** 行）或 `send_event_raw`（第 2130 行）→ `send_event_raw_with_persistence(event, persist)`（第 2148 行）：
  - 先 `mcp_runtime.observe_event(&event.msg)`，
  - `if persist { persist_rollout_items(&[RolloutItem::EventMsg(msg)]) }`（第 2152 行，写 JSONL，见数据流②），
  - 再 `send_event_raw`... 实际把事件投到 `tx_event`。
- `tx_event` 的订阅方通过 `SessionIo::next_event`（第 896 行）`rx_event.recv()` 消费；`CodexThread::next_event`（`codex-rs/core/src/codex_thread.rs` 第 463 行）暴露给上层（app-server）。
- `EventMsg` 枚举定义：`codex-rs/protocol/src/protocol.rs` 第 **1288–1498** 行（`TurnStarted`/`TurnComplete`/`AgentMessage`/`AgentReasoning*`/`ExecApprovalRequest`/`ExecCommand*`/`TurnAborted`/`RawResponseItem*`/`ItemStarted`/`ItemCompleted`/...）。

**F. 一 Session 至多一个运行中 Task（并发模型）**
- `Session`（`codex-rs/core/src/session/session.rs` 第 **38** 行）持有 `active_turn: Mutex<Option<ActiveTurn>>`（第 61 行）和 `input_queue: InputQueue`（第 63 行）。
- `ActiveTurn`（`codex-rs/core/src/state/turn.rs` 第 **32** 行）结构是 `{ task: Option<RunningTask>, turn_state: Arc<Mutex<TurnState>> }` —— **单槽**。`task` 为 `Some` 时有任务在跑；`None` 时是"已占位但尚无任务"的 idle 态。
- 因此：**每个 `Session` 同一时刻至多执行一个 `Task`**（`TaskKind`：`Regular` / `Review` / `Compact`，第 68 行）。这是"一 Session 至多一个运行中 Task"的代码体现。
- 并发体现在**跨 Session**：`ThreadManager`（`codex-rs/core/src/thread_manager.rs` 第 **218** 行）把每个线程建模为 `Arc<CodexThread>`，放进 `ThreadManagerState.threads: Arc<RwLock<HashMap<ThreadId, Arc<CodexThread>>>>`（第 **336–337** 行）。每个线程跑独立的 `submission_loop`/`session_loop`，`ThreadManager` 只是管理集合、提供 `start_thread`（第 905 行）、`send_op`（第 1438 行）、`subscribe_thread_created`（广播，第 778 行）。所以"多线程并发达"是在**线程之间**，不是线程内部。
- `RunningTask` 含 `done: Arc<Notify>`、`cancellation_token`、`handle: AbortOnDropHandle<()>`（`state/turn.rs` 第 74 行）——是 `interrupt`(Op) 可被打断、可被 drop 取消的载体。

### 2.2 数据流② rollout 追加路径（JSONL append-only + state_db 镜像）

**命题**：会话事件以 append-only JSONL 落盘（主存储）；状态 SQLite（`codex_state`/`state_db`）是**元数据镜像/回填**，不是主记录；写入由**单一写者任务 + tokio mpsc + oneshot ack**。

**A. 事件 → 写 JSONL**
- `send_event_raw_with_persistence`（`codex-rs/core/src/session/mod.rs` 第 2148 行）→ `persist_rollout_items(&items)`（第 3717 行）→ `live_thread.append_items(items)`。
- `LiveThread::append_items`（`codex-rs/thread-store/src/live_thread.rs` 第 **203** 行）→ `thread_store.append_items(AppendThreadItemsParams {...})`。对 `LocalThreadStore`，底层是 `RolloutRecorder`。
- `RolloutRecorder::record_canonical_items`（`codex-rs/rollout/src/recorder.rs` 第 **953** 行）`self.tx.send(RolloutCmd::AddItems(items.to_vec()))`。
- `RolloutRecorder`（第 **86** 行）：`{ tx: Sender<RolloutCmd>, writer_task: Arc<RolloutWriterTask>, rollout_path }`。`RolloutCmd`（第 124 行）为 `AddItems` / `Persist{ack: oneshot}` / `Flush{ack: oneshot}` / `Shutdown{ack: oneshot}`。
- 单一写者：`rollout_writer(mut state, mut rx)`（第 **1820** 行）`while let Some(cmd) = rx.recv().await` 依次处理 `AddItems → state.add_items + flush_if_materialized`、`Persist → ack.send(state.persist())`、`Flush → ack.send(state.flush())`、`Shutdown → ack.send(state.shutdown())`。所以**所有写 JSONL 的操作都串行经过这一个写者任务**，用 `tokio::sync::mpsc` 传递命令，用 `oneshot` 回 ack 实现"flush/persist 完成后才返回"。

**B. JSONL 追加细节**
- `JsonlWriter::write_rollout_item`（第 1949 行）写 `RolloutLineRef { timestamp, ordinal, item }`；`append_rollout_item_to_path`（第 1886 行）用于未装载线程的元数据更新（会 `materialize_rollout_for_append` 压平压缩文件再 append，并保证以 `\n` 结尾）。
- 文件名形如 `rollout-<timestamp>-<conversation_id>.jsonl`，在 `~/.codex/sessions/`（常量 `SESSIONS_SUBDIR`，`codex-rs/rollout/src/lib.rs` 第 67 行）。

**C. state_db / SQLite 的角色**
- `state_db` 在 `codex-rs/rollout/src/state_db.rs`：`StateDbHandle = Arc<codex_state::StateRuntime>`（第 **29** 行）；入口 `init(config)`（第 45 行）→ `try_init`（第 60 行）→ `try_init_with_roots`，内部做**回填（backfill）**：把 JSONL rollout 里的线程元数据提取并镜像进 SQLite。
- `codex_state` crate（`codex-rs/state/src/lib.rs`）：<b>刻意小而聚焦</b>——"extract rollout metadata from JSONL and mirror it into local SQLite；backfill orchestration lives in codex-core"（第 3–5 行注释）。核心类型 `StateRuntime`（`state/src/runtime.rs`）、`SqliteConfig`、`ThreadMetadata`、目标/队列存储（`SqliteQueueStore`）。SQLite 需含 WAL-reset 修复（lib.rs 第 7–9 行的 `assert`，依赖 `libsqlite3-sys >= 3.51.3`）。
- Core 侧的桥：`codex-rs/core/src/state_db_bridge.rs` 第 **6** 行 `pub async fn init_state_db(config: &Config)` → `rollout_state_db::init(config)`。
- 结论：**JSONL 是事实源（event log），SQLite 是它的可查询元数据镜像**。恢复会话时**主要的回放数据来自 JSONL**（见 D），SQLite 用于线程列表/元数据/队列等快速查询。

**D. 恢复会话怎么读回来**
- `RolloutRecorder::get_rollout_history`（`rollout/src/recorder.rs` 第 1074 行）与 `load_rollout_items`（第 1009 行）从 JSONL 读出 `InitialHistory`/`RolloutItem`。`decode_rollout_line`（`rollout/src/lib.rs` 第 45 行）处理 `serde_json/arbitrary_precision` 的浮点兼容问题。
- 恢复进 session：`Session::record_initial_history`（`core/src/session/mod.rs` 第 1328 行）→ 对 `InitialHistory::Resumed(row)`/`Forked(...)` 调 `apply_rollout_reconstruction`（第 1463 行），用 `reconstruct_history_from_rollout` 重建 `ContextManager`，并回填 token 用量、`auto_compact_window`、`world_state`。
- 线程恢复的入口：`ThreadManager::resume_thread_from_rollout(...)`（`codex-rs/core/src/thread_manager.rs` 第 974 行）。

### 2.3 数据流③ 审批流（ExecApproval）

**命题**：`ExecApprovalRequest` 类事件在"工具需要用户许可时"产生；`AskForApproval` 各模式决定"是否要审 / 走 Guardian 还是 User"；批准/拒绝经 `oneshot` 通道返回执行点。

**A. 产生点**
- 触发：工具执行需要许可时调 `Session::request_approval(action, ctx)`（`codex-rs/core/src/tools/approvals.rs` 第 **485** 行）。它先跑 `run_permission_request_hooks`（第 503 行），hook 未决则 `request_reviewer_approval`（第 549 行）。
- reviewer 判定：`ApprovalReviewer::for_policy`（第 432 行）→ `routes_approval_policy_to_guardian(approval_policy, reviewer)` 为真则 `Guardian`，否则 `User`。
- `AskForApproval` 各模式（`codex-rs/protocol/src/protocol.rs` 第 **916–939** 行）：
  - `UnlessTrusted`（untrusted 项目需审批，除非 execpolicy 允许）
  - `OnRequest`（默认；模型决定何时问）
  - `Granular(GranularApprovalConfig)`（细粒度开关，字段为 `true` 才允许该类命令）
  - `Never`（从不向用户询问，直接返回失败给模型）
- `request_user_approval`（`core/src/tools/approvals.rs` 第 **657** 行）对命令/补丁/MCP/网络等分支处理，命令类走 `request_command_approval`。

**B. `request_command_approval`（核心通道）**
- `Session::request_command_approval(...)`（`codex-rs/core/src/session/mod.rs` 第 **2371** 行）：
  1. `(tx_approve, rx_approve) = oneshot::channel()`（第 2391 行）。
  2. 把 `tx_approve` **先**插入 `turn_state.pending_approvals`，key = `effective_approval_id`（=`approval_id` 或 `call_id`，第 2397 行）。
  3. 构造 `EventMsg::ExecApprovalRequest(ExecApprovalRequestEvent {...})`（第 2433 行）并 `self.send_event(turn_context, event)`（第 2451 行）→ 客户端收到事件。
  4. `rx_approve.await.unwrap_or(ReviewDecision::Abort)`（第 2452 行）——**执行点在这里挂起**。
  5. `TurnState.pending_approvals` 是 `HashMap<String, oneshot::Sender<ReviewDecision>>`（`codex-rs/core/src/state/turn.rs` 第 90 行；插入/移除见第 112/120 行）。
- 类似的还有 `request_patch_approval`（第 2459 行，发 `ApplyPatchApprovalRequest`）。

**C. 决定返回通道**
- 客户端把决定作为 `Op::ExecApproval { id, turn_id, decision }`（`protocol.rs` 第 598 行）塞回提交队列。
- `submission_loop` 匹配 `Op::ExecApproval` → `exec_approval(&sess, approval_id, turn_id, decision)`（`core/src/session/handlers.rs` 第 **174** 行；`Op::PatchApproval` → 第 611 行 `patch_approval`）。
- `exec_approval`：若 `decision == ReviewDecision::Abort` 则 `sess.interrupt_task()`（第 199 行），否则 `sess.notify_approval(&approval_id, other)`（第 201 行）。
- `Session::notify_approval(&self, approval_id, decision)`（`codex-rs/core/src/session/mod.rs` 第 **2926** 行）：`turn_state.remove_pending_approval(approval_id)`，把 `decision` `tx_approve.send(decision)` 回发（第 2938–2940 行）。若找不到，`warn!("No pending approval found for call_id: {approval_id}")`。
- 于是 `rx_approve.await` 收到 `decision`，`request_command_approval` 返回；上层把 `ReviewDecision` 转成 `ToolError::Rejected`/`Approved` 等（`approvals.rs` `ApprovalResolution::into_tool_result` 第 455 行）。

**D. 工具侧如何到达审批**
- shell/unified_exec 等 handler 内部经由 `tools/approvals.rs` 的 `request_approval` 门；网络审批走 `tools/network_approval.rs`；MCP 走 `core/src/mcp_tool_call.rs` 的 `request_mcp_tool_user_approval`；补丁走 `request_patch_approval`。这些最终都汇入 `pending_approvals` + `oneshot` 通道模型。

---

## 3. app-server 子系统（对魔改最重要）

### 3.1 传输方式与成熟度
- `AppServerTransport` 枚举：`codex-rs/app-server-transport/src/transport/mod.rs` 第 **75** 行：
  - `Stdio`、`UnixSocket { socket_path }`、`WebSocket { bind_address }`、`Off`。
  - 解析：`from_listen_url`（第 116 行），默认 `DEFAULT_LISTEN_URL = "stdio://"`（第 114 行）。
  - `--listen` 支持 `stdio://` / `unix://` / `unix://PATH` / `ws://IP:PORT` / `off`（错误信息第 94 行）。
- 各启动器：
  - **stdio**：`start_stdio_connection`（`codex-rs/app-server-transport/src/transport/stdio.rs` 第 24 行）。单客户端（`lib.rs` 第 723 行 `single_client_mode = matches!(&transport, AppServerTransport::Stdio)`），走 stdin/stdout 帧。
  - **unix socket**（控制平面）：`start_control_socket_acceptor`（`transport/unix_socket.rs` 第 24 行）。控制 socket 路径 `~/.codex/app-server-control/app-server-control.sock`（`transport/mod.rs` 第 54–63 行）。主要用于远端控制 / 内嵌。
  - **websocket**：`start_websocket_acceptor`（`transport/websocket.rs` 第 **129** 行）。用 `axum` 的 `WebSocketUpgrade` + `tokio-tungstenite`，可同时跑 axum 与 tungstenite 两套消息适配（`AppServerWebSocketMessage` trait，第 242 行）。**成熟度：真实可用**，有独立的出站队列（`WEBSOCKET_OUTBOUND_CHANNEL_CAPACITY = 32*1024`，第 48 行），并带认证（`auth.rs`：`WebsocketAuthPolicy`、`AppServerWebsocketAuthSettings`）。websocket 客户端可"短暂滞后于正常输出突发"，故出站容量特意大于 `CHANNEL_CAPACITY`（第 49 行 `assert!`）。
- 出站路由：`route_outgoing_envelope`（`codex-rs/app-server/src/transport.rs` 第 200 行）按 `OutgoingEnvelope::ToConnection` / `Broadcast` 投递；`send_message_to_connection`（第 136 行）在队列满时对"可断连"的连接调用 `disconnect_connection`（第 159–164 行）——这是对慢消费者的背压处置。

### 3.2 JSON-RPC 方法面（以代码为准）
- 注意：`codex-rs/app-server-protocol/src/rpc.rs` 第 **1–2** 行注释："We do not do true JSON-RPC 2.0, as we neither send nor expect the `jsonrpc: 2.0` field." 所以 wire 上不带 `jsonrpc` 字段。
- 主枚举：`ServerRequest`（请求）与 `AppServerNotification`（通知）定义在 `codex-rs/app-server-protocol/src/protocol/common.rs`。**主要方法名**（从该文件提取，`=> "..."` 那一列是 wire 名）：

| 类别 | wire 方法 |
| --- | --- |
| 初始化/诊断 | `initialize`、`server/diagnostics` |
| 线程生命周期 | `thread/start`、`thread/resume`、`thread/fork`、`thread/archive`、`thread/delete`、`thread/unarchive`、`thread/revert`、`thread/rollback` |
| 线程元数据 | `thread/name/set`、`thread/metadata/update`、`thread/settings/update`、`thread/memoryMode/set`、`thread/status/changed`(通知) |
| Turn | `turn/start`、`turn/steer`、`turn/interrupt` |
| 线程内建工具 | `thread/queue/*`、`thread/Section/*`、`thread/backgroundTerminals/*`、`thread/compact/start`、`thread/shellCommand` |
| 项目/FS | `project/*`、`fs/*`(readFile/writeFile/watch/...) |
| MCP | `mcpServer/oauth/login`、`config/mcpServer/reload`、`mcpServerStatus/list`、`mcpServer/resource/read`、`mcpServer/tool/call` |
| 模型/特性 | `model/list`、`modelProvider/capabilities/read`、`experimentalFeature/list`、`experimentalFeature/enablement/set` |
| 审批/工具响应 | `item/commandExecution/requestApproval`、`item/fileChange/requestApproval`、`item/tool/requestUserInput`、`item/permissions/requestApproval`、`item/tool/call`、`mcpServer/elicitation/request` |
| 其他 | `environment/*`、`skills/*`、`hooks/list`、`marketplace/*`、`plugin/*`、`app/*`、`account/*`、`feedback/upload`、`config/*`、`remoteControl/*`、`windowsSandbox/*`、`collaborationMode/list`、`attestation/generate` |

- `tad`/`turn/start` 的处理：`codex-rs/app-server/src/request_processors/turn_processor.rs` `turn_start`（第 179 行）→ `turn_start_inner`（第 **478** 行）：`load_thread` → `start_or_steer_turn(...)`（第 553 行）→ `ThreadManager`/`CodexThread` 提交。
- `thread/start` 的处理：`codex-rs/app-server/src/request_processors/thread_processor.rs` `thread_start`（第 500 行）→ `thread_start_inner`（第 **1056** 行）→ `thread_manager.start_thread(StartThreadOptions {...})`（第 1388 行）→ 得到 `NewThread`。
- 请求分派入口：`codex-rs/app-server/src/connection_rpc_gate.rs`（把 JSON-RPC 请求映射到相应 processor）。

### 3.3 背压语义
- `app-server-transport/src/transport/mod.rs`：`CHANNEL_CAPACITY = 128`（第 25 行）。错误码：`INTERNAL_ERROR_CODE = -32603`、`OVERLOADED_ERROR_CODE = -32001`（第 51–52 行）。
- 慢消费者背压：`transport.rs` 出站为 `mpsc::Sender<QueuedOutgoingMessage>`，当 `/try_send` 满且连接可断连时强制断连（第 157–164 行）；`Broadcast` 对未初始化/已 opt-out 的连接跳过（第 218–226 行）。
- 注意 `CHANNEL_CAPACITY=128` 是**透传层**的容量；而 Core 侧 `Submission` 队列容量是 512、`tx_event` 是**无界**的（见 2.1）。这三者量级/语义不同：Core 到 app-server 的事件流不被背压，app-server 出站则受 128/容量与队列满强制断连约束。

### 3.4 与 core 的线程管理关系
- app-server 持有 `ConfigManager`、`AuthManager` 等，最终通过 `ThreadManager`（来自 `codex_core`）创建/查找线程（`thread/start` → `start_thread`；`turn/start` → `CodexThread::start_or_steer_turn`）。
- 每个 app-server 连接的入站请求经 `connection_rpc_gate` 转成对 `CodexThread.io.submit(...)` 的调用；出站事件经 `CodexThread::next_event` → `SessionIo::next_event` 消费并从 `transport` 写出。
- 启动入口：`codex-rs/app-server/src/lib.rs` `run_main_with_transport_options`（第 **449** 行）创建三组 mpsc 通道（`transport_event_tx`、`outgoing_tx`、`outbound_control_tx`），然后 `match &transport` 启动对应 acceptor（第 728 行起），并基于 `SessionSource` 区分来源。
- 进程入口（二进制）：`codex-rs/app-server/src/main.rs`（解析 CLI，调用 `run_main`，`run_main` 在 `lib.rs` 第 402 行）。

---

## 4. MCP 支持

**rmcp-client 在哪**：`codex-rs/rmcp-client/`。它是 `rmcp` SDK 的封装，提供 stdio 启动（`stdio_server_launcher.rs`、`local_stdio_transport.rs`）、streamable-http（`http_client_adapter.rs`、`streamable_http_retry.rs`）、in-process（`in_process_transport.rs`）、OAuth（`oauth.rs`）、elicitation（`elicitation_client_service.rs`）、`program_resolver.rs` 等。

**配置里 `mcp_servers` 如何被加载成工具**：
- 配置定义：`codex-rs/config/src/config_toml.rs` 第 **264** 行 `pub mcp_servers: HashMap<String, McpServerConfig>`（用自定义反序列化，见第 262 行注释）；`McpServerConfig` 类型在 `config/src/mcp_types.rs`。
- 绑定层：`codex-rs/codex-mcp/src/binding.rs` `pub struct McpBinding`（第 **31** 行），持有 `McpConfig`、`connection_manager`、服务器目录 `mcp_server_catalog`；`pub fn tools(&self) -> &[ToolInfo]`（第 80 行）暴露已连接服务器暴露的工具。`effective_mcp_servers` 在 `core/src/session/mod.rs`（导入、用于 `required_mcp_servers_for_input`）。
- 把 MCP 工具塞进 `ToolRegistry`：`core/src/tools/spec_plan.rs::build_tool_router`（第 **121** 行）→ `mcp_handler_cache.append_mcp_tools(mcp, ...)`（`core/src/mcp_tool_exposure.rs` 第 **37** 行，注意该文件位于 `core/src/` 而非 `core/src/tools/`）把每个 MCP 工具包装为 `McpHandler`，`registry.register_external_with_exposure(handler, tool_exposure)`。

**工具名冲突 / 白名单逻辑位置**：
- 冲突：`ToolRegistry::register_external_with_exposure`（`core/src/tools/registry.rs` 第 **347** 行）——`shell_command` 为保留名（默认命名空间）直接跳过并记录碰撞；同名 MCP/外部工具**跳过**并 `record_collision`（第 366–374 行）。`first_collision()`（第 382 行）可被上层查询。
- "白名单"/暴露度策略：`apply_mcp_tool_exposure_policy`（`core/src/tools/spec_plan.rs` 第 **178** 行）结合 `mcp.config().mcp_server_catalog.server(server_name)` 与 `core/src/mcp_tool_exposure.rs` 的 exposure 计算，决定 `ToolExposures`（`DIRECT`/`DEFERRED`/`CODE_MODE`/`ALL` 差集）与是否放行。`core/src/mcp_tool_exposure.rs` 里还有 `filter_non_codex_apps_mcp_tools_only` / `filter_codex_apps_mcp_tools`（第 149/157 行）做 app 粒度过滤。
- namespace 暴露描述：`ToolRegistry::deferred_tool_namespaces`（`registry.rs` 第 400 行）。

---

## 5. 构建双轨注记（详见 BUILD.md）

该仓库以 **Cargo 为主、Bazel 并行** 的双轨构建：
- `justfile` 在仓库根，含格式化/安装等目标（`fmt`、`install` 等）。
- Bazel 侧：根目录有 `MODULE.bazel`（bzlmod 模块定义）与 `MODULE.bazel.lock`（约 1.5 MB 的依赖锁定），`defs.bzl` / `rbe.bzl`（rules_rust / rules_cc 封装），以及一个庞大的 `patches/` 目录（针对 abseil、bzip2、rules_rust、rusty_v8、v8、webrtc-sys、zstd-sys 等打补丁，多为 Windows/MSVC/sysroot/direct-link 兼容）。
- 因此魔改时需注意：**只改 `codex-rs/` 下源码还不够**，Bazel 构建若被用到，`MODULE.bazel.lock` 与 `patches/` 里的补丁可能需要同步；细节留给 `BUILD.md`，本文不展开。

---

## 6. 附录：精读中发现的"值得注意"事实

- **提交队列有界，事件流无界**：`Submission` 队列容量 512（`session/mod.rs:458`），但 `tx_event` 是 `async_channel::unbounded()`（`session/mod.rs:531`）。所以 Core 侧"事件背压"其实很弱，真正的背压发生在 app-server 出站（容量 128 / websocket 32k，满则强制断连）。
- **websocket 不是桩**：`app-server-transport/websocket.rs` 是完整可用的实现（axum upgrude + tungstenite 双消息适配），带认证与独立大出站队列，成熟度高。
- **单会话单任务的并发模型是"结构强制"而非"配置"**：`Session.active_turn` 只是单槽 `Mutex<Option<ActiveTurn>>`，天然保证一个 `Session` 同时只有一个 `Task`；多并发在**线程之间**（`ThreadManager.threads`）。
- **工具冲突处理不对称**：trusted 工具重名会 `error_or_panic`（`registry.rs:325`），而 external（含 MCP）重名只 `warn` + 跳过 + 记录碰撞。
- **JSON-RPC 并非严格 2.0**：不带 `jsonrpc:"2.0"` 字段（`rpc.rs:1`）；-32001/-32603 是服务器自定义错误码。
- **JSONL 是主存储，SQLite 是镜像/回填**：事件先写 JSONL（单一写者任务），`codex_state` 从 rollout 提取元数据回填 SQLite，恢复时以 JSONL 事实源重建。
- **审批是"挂起的 oneshot"，不是全局流水线**：`pending_approvals` 以 `approval_id`/`call_id` 为 key 存放在当前 `TurnState`，`notify_approval` 精确回发，跨 turn 不共享。
