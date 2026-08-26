# CRATES —— runtime/codex-rs workspace 花名册

> 性质：§6.1 裁剪的**执行账本**。Phase 1 裁剪时逐 crate 记账：
> 「保留」者一行职责说明；「删除」者附理由。账外增删 = 缺陷。
>
> 权威来源：`runtime/codex-rs/Cargo.toml` `[workspace.members]`（锚点 rust-v0.149.1，共 133 个成员）。
> 分组导航与三条数据流见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 状态图例

- **保留（核心）**：魔改不动区或必经之路（§6.4）
- **保留（待裁）**：暂留，Phase 1 逐个裁决
- **删除**：已裁掉（记账：日期 + 理由）

## Phase 0 现状

收编即锚点原样，133 个成员全部在册、状态一律为「保留（待裁）」。
完整清单以 Cargo.toml 为准，此处不重复罗列；Phase 1 裁剪启动后按批登记：

| 批次 | crate | 动作 | 理由 | 日期 |
|---|---|---|---|---|
| P1-0（核实批，未执行删除） | cloud-tasks / cloud-tasks-client / cloud-tasks-mock-client | 拟删除（已核实依赖面：cli 直依赖 codex-cloud-tasks；tui 亦牵连） | §6.1 预定名单；云端任务非单校私有化形态 | 2026-08-25 |
| P1-0（核实批，未执行删除） | tui | 拟删除（被 cli 与 cloud-tasks 依赖——删除需同步改 cli 依赖声明，属首批 DELTA 记账项） | 唯一前门是 app-server；教育产品无终端 UI 需求 | 2026-08-25 |
| P1-0（核实批，未执行删除） | connectors(+ext/connectors) | 拟删除（被 codex-mcp/tools/core 共 58 处源码引用，牵动 MCP 工具暴露/审批/路由，独立手术批处理） | §6.1 预定名单 | 2026-08-25 |
| P1-0（核实批，未执行删除） | linux-sandbox / windows-sandbox / network-proxy / bwrap | 暂缓（windows-sandbox 被 core/tui/sandboxing/cli/network-proxy 广泛引用；bwrap 同属沙箱抽象，一并暂缓） | 删除牵动 sandboxing 抽象层，须先设计替代或保留门面 | 2026-08-25 |
| P1-1（执行批 ✅） | v8-poc | **删除**（members/workspace.deps/cargo-shear 三处 manifest + 目录；零反向依赖已核实）；连带效果：workspace 全量 check 不再拉 V8 编译（v8 现仅剩 code-mode-runtime 引用） | code-mode/v8 全家范畴（§6.1） | 2026-08-25 |
| P1-1（执行批 ✅） | code-mode-host | **删除**（member 移除 + 目录；独立二进制零反向依赖） | code-mode/v8 全家范畴（§6.1） | 2026-08-25 |
| P1-1（执行批 ✅） | thread-manager-sample | **删除**（member 移除 + 目录；sample 类，零反向依赖） | 示例工程不随产品分发 | 2026-08-25 |
| P1-2（前门批 ✅） | cli / tui / cloud-tasks / cloud-tasks-client / cloud-tasks-mock-client | **删除五件套**（依赖闭包核实：cli→cloud-tasks→tui 链外无其他依赖者；manifest 移除 5 members + 3 workspace.deps）。随批退役 app-server 测试：code_mode_host.rs、executor_mcp.rs（codex bin 作 executor）、selected_capability_stack.rs（cargo_bin("codex")）、turn_start 的 code_mode analytics 用例、imagegen 的 code_mode_only 用例 | 教育产品唯一前门 = app-server；终端 UI/云端任务/交互式 CLI 均不随产品分发 | 2026-08-25 |
| P1-3a（code-mode 手术批 ✅） | code-mode / code-mode-protocol / code-mode-runtime | **删除三件套**（members + workspace.deps + 目录）。连带拆除：Feature 枚举 5 个 CodeMode* 变体与配置结构、ToolOutput::code_mode_result trait 方法、ToolCallSource/ExecutedToolCallRecorder 单元格追踪、spec_plan 工具路由的 code-mode 分支、rollout-trace 仅保留回放数据结构；**workspace 全量 check 自此不再拉 V8** | code-mode/v8 全家范畴（§6.1）；DeepSeek 单模型单会话下 code-mode 执行面永不激活 | 2026-08-25 |
| P1-3b（connectors 手术批 ✅） | connectors / ext/connectors | **删除两件套**（members + workspace.deps + 目录）。连带拆除：`ConnectorRuntimeManager` MCP 缓存通道（McpRuntimeInput/AsyncManagedClient/cached_server_info）、`ConnectorSnapshot`、`AppToolPolicyEvaluator`（内联为 codex-mcp 本地实现）、`parse_plugin_app_config`（迁入 codex-plugin）、app 认证 elicitation 流程、core 的 connectors 列表/缓存模块、`selected_plugin_connector_sources` 死代码链、tools 的 `DiscoverableTool::Connector` 变体；58 处源码引用全部清除 | §6.1 预定名单；全部依赖 ChatGPT 认证，教育产品永不激活 | 2026-08-26 |
| P1-4a（沙箱批第一批 ✅） | bwrap / linux-sandbox | **删除两件套**（members + workspace.deps + 目录，含 vendor/bubblewrap C 源码）。连带拆除：`SandboxType::LinuxSeccomp` 变体、`codex-linux-sandbox` 派发/别名/路径管线（arg0/core/exec-server/app-server/exec/codex-mcp/mcp-server）、landlock/bwrap 门面模块、`codex_linux_sandbox_exe` / `use_legacy_landlock` 两字段跨 crates 全清、`Feature::UseLegacyLandlock`(Deprecated) / `UseLinuxSandboxBwrap`(Removed) 标志、`SystemError::LandlockSandboxExecutableNotProvided`、exec-server 的 sandboxed-file-system Require 失败关闭路径保留（fail-closed）；**workspace 不再编译 landlock/seccomp/seccompiler/bubblewrap 依赖链** | §6.1（linux-sandbox/windows-sandbox"内部函数调用无需 OS 沙箱"）；容器化部署下 OS 沙箱冗余；network-proxy/windows-sandbox 留后续批 | 2026-08-26 |

P1-1 验证：`cargo check -p codex-app-server` 全绿（基线 2m12s → 删除后复验通过）；
`--workspace` 全量检查因 code-mode-runtime→v8 的既有依赖仍需 V8 归档，
随 connectors/code-mode 手术批一并解决。133 → 130 members。
P1-2 验证：`cargo check -p codex-app-server` 全绿；app-server 测试 **949 passed 全绿**
（RUST_MIN_STACK=8388608）；core 2260 passed，唯一失败
（blocking_snapshot_waits_for_starting_environment）经锚点 worktree 对照确认为
**上游自带红测试**，与本批无关。130 → 125 members。
P1-3 验证：`cargo check --workspace` 全绿（不再拉 V8）；core lib 2163 passed（2 个
全局 tracing/时序敏感测试在并行全量下偶红、隔离全绿，经对照与本批无关）；codex-mcp
176 passed、core-plugins 409 passed、app-server `all` 915 passed 全绿。
P1-3 期间修复两处**上批遗留红测试**：plugin_install 三个 remote-oauth 用例缺
`mount_remote_plugin_install` mock（上批删 with_apps_needing_auth 时误删）、
recommended_plugins 断言 request_plugin_install 工具存在（该工具已随 tool_suggest 裁剪）。
另退役 35 个已删功能的过期集成测试（CLI 二进制/apps/tool_suggest，DELTA.md D-013），
并修复三处上批误删（插件能力段恢复、McpCallEvent derive、ProviderAuthCommandFixture cfg）。
core `all` 集成套件并行全量 68 红均为负载脆弱性（隔离全绿；HEAD 基线同为 173 红）。
125 → 120 members。
P1-4a 验证：`cargo check -p codex-app-server` 全绿；`cargo test --no-run` 全 workspace
0 错误；受影响 crate 定向测试全绿（sandboxing 82 / arg0 6 / exec-server lib 235 / features
34 / codex-mcp 172 / file-system 18）；core lib 2168 仅剩 2 个已知负载脆弱红
（blocking_snapshot 上游自带红 + post_sampling 时序敏感，隔离全绿；schema 畸形
config_schema_matches_fixture 已随 regeneraton 修复）。退役 linux OS 沙箱的过期集成测试
（exec sandbox 套件、arg0 别名、fs_sandbox bwrap 用例、hostile-helper 用例等，DELTA.md
D-019）。120 → 118 members。

### 后续批次规划

- **P1-4b**：windows-sandbox-rs + network-proxy（本轮保留；windows 纯 Windows 面、
  network-proxy 为 Restricted 网络策略核心，各自独立批）。
- 后续可裁：realtime 全家 · exec 人类输出面 · 编码类 prompt 文件 · apply_patch 默认工具。

### 预定删除名单（§6.1 规划，Phase 1 逐个核实依赖后执行）

cloud-tasks 全家 · realtime 全家 · code-mode/v8 全家 · connectors ·
TUI（唯一前门是 app-server）· exec 人类输出面 · apply_patch 默认工具 ·
编码类 prompt 文件 · windows-sandbox（内部函数调用无需 OS 沙箱；network-proxy 独立评估）

### 预定新增（§6.3，均须配 BUILD.bazel——D8 双轨义务）

`edu-tools`（领域工具参数校验/白名单/`_provenance` 注入）·
`school-authz`（教师↔班级权限断言/token 校验/身份注入 MCP 连接）
