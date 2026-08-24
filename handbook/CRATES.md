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
| — | （尚未开始裁剪） | | | |

### 预定删除名单（§6.1 规划，Phase 1 逐个核实依赖后执行）

cloud-tasks 全家 · realtime 全家 · code-mode/v8 全家 · connectors ·
TUI（唯一前门是 app-server）· exec 人类输出面 · apply_patch 默认工具 ·
编码类 prompt 文件 · linux-sandbox/windows-sandbox（内部函数调用无需 OS 沙箱；
是否连带删其依赖评估后定）

### 预定新增（§6.3，均须配 BUILD.bazel——D8 双轨义务）

`edu-tools`（领域工具参数校验/白名单/`_provenance` 注入）·
`school-authz`（教师↔班级权限断言/token 校验/身份注入 MCP 连接）
