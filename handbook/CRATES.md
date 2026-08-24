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
| P1-0（核实批，未执行删除） | v8-poc / connectors(+ext/connectors) | 拟删除（v8-poc 仅 workspace 成员关系无 crate 反依赖；connectors 被 codex-mcp/tools/core 引用，删除面大，排第二批评估） | §6.1 预定名单 | 2026-08-25 |
| P1-0（核实批，未执行删除） | linux-sandbox / windows-sandbox / network-proxy | 暂缓（linux-sandbox 被 arg0 引用；windows-sandbox 被 core/tui/sandboxing/cli/network-proxy 广泛引用——内部函数调用场景无需 OS 沙箱属实，但删除牵动 sandboxing 抽象层，须先设计替代或保留 sandboxing 门面） | §6.1 名单标注「连带删其依赖评估后定」 | 2026-08-25 |

**执行前提**：本机开发环境暂无 Rust 工具链，删除动作必须等 cargo 可用后
逐批「删 → `just test` 全绿 → CRATES.md 记账 → DELTA.md 开账（cli 依赖声明变更
即首批源码分歧）」推进。P1-0 批次只做依赖面核实，不改动任何源码。

### 预定删除名单（§6.1 规划，Phase 1 逐个核实依赖后执行）

cloud-tasks 全家 · realtime 全家 · code-mode/v8 全家 · connectors ·
TUI（唯一前门是 app-server）· exec 人类输出面 · apply_patch 默认工具 ·
编码类 prompt 文件 · linux-sandbox/windows-sandbox（内部函数调用无需 OS 沙箱；
是否连带删其依赖评估后定）

### 预定新增（§6.3，均须配 BUILD.bazel——D8 双轨义务）

`edu-tools`（领域工具参数校验/白名单/`_provenance` 注入）·
`school-authz`（教师↔班级权限断言/token 校验/身份注入 MCP 连接）
