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

P1-1 验证：`cargo check -p codex-app-server` 全绿（基线 2m12s → 删除后复验通过）；
`--workspace` 全量检查因 code-mode-runtime→v8 的既有依赖仍需 V8 归档，
随 connectors/code-mode 手术批一并解决。133 → 130 members。
P1-2 验证：`cargo check -p codex-app-server` 全绿；app-server 测试 **949 passed 全绿**
（RUST_MIN_STACK=8388608）；core 2260 passed，唯一失败
（blocking_snapshot_waits_for_starting_environment）经锚点 worktree 对照确认为
**上游自带红测试**，与本批无关。130 → 125 members。

### 后续批次规划

- **P1-3 手术批（剩余最大件）**：connectors 家（58 处引用）+ code-mode 全家
  （含 code-mode-runtime 的 v8 依赖、core/tools/app-server 的 342 处引用）——
  深度织入 spec_plan 工具暴露路径，需独立会话专项处理，全量测试护航；
- **P1-4 沙箱批**：linux/windows-sandbox + bwrap + network-proxy（先设计 sandboxing
  门面保留方案）。

### 预定删除名单（§6.1 规划，Phase 1 逐个核实依赖后执行）

cloud-tasks 全家 · realtime 全家 · code-mode/v8 全家 · connectors ·
TUI（唯一前门是 app-server）· exec 人类输出面 · apply_patch 默认工具 ·
编码类 prompt 文件 · linux-sandbox/windows-sandbox（内部函数调用无需 OS 沙箱；
是否连带删其依赖评估后定）

### 预定新增（§6.3，均须配 BUILD.bazel——D8 双轨义务）

`edu-tools`（领域工具参数校验/白名单/`_provenance` 注入）·
`school-authz`（教师↔班级权限断言/token 校验/身份注入 MCP 连接）
