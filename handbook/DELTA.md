# DELTA —— 相对锚点的全部源码分歧台账

> 性质：魔改仓库与上游的**每一处**源码分歧都在此登记（位置/内容/原因），
> ADR 条目式追加，永不重写历史条目。任何人 diff 我们的 runtime/ 与上游锚点时，
> 每一处差异都应能在本文件找到对应行——反之亦然（账外分歧 = 缺陷）。
>
> 维护纪律：第一笔源码修改起逐笔开账；cherry-pick 上游修复不产生新条目
> （那是向锚点收敛），但若 cherry-pick 与既有分歧冲突需手工对位时补记一条。

## 锚点（2026-08-24 登记）

| 项 | 值 |
|---|---|
| 上游仓库 | openai/codex（Apache-2.0） |
| 锚定 tag | `rust-v0.149.1` |
| 锚定 commit | `ff29a44391deccde0aba0f8390337d7f3c319ea4`（2026-08-24 发布当日 latest stable） |
| 收编方式 | 本地克隆 checkout 到锚点 → 去 `.git` 整体搬入为 `runtime/`（§2.1 铁律：内部一字不动） |
| 收编日期 | 2026-08-25 |

## 分歧台账

| # | 位置 | 内容 | 原因 | 日期 |
|---|---|---|---|---|
| D-001 | `codex-rs/Cargo.toml` `[workspace.members]` | 移除 `"v8-poc"`、`"code-mode-host"`、`"thread-manager-sample"` 三项 | §6.1 裁剪（CRATES.md P1-1 批）；三 crate 零反向依赖，目录一并删除 | 2026-08-25 |
| D-002 | `codex-rs/Cargo.toml` `[workspace.dependencies]` | 移除 `codex-v8-poc = { path = "v8-poc" }` | 同上（随成员删除的声明清理） | 2026-08-25 |
| D-003 | `codex-rs/Cargo.toml` `[workspace.metadata.cargo-shear]` ignored | 移除 `"codex-v8-poc"` | 同上 | 2026-08-25 |
| D-004 | `codex-rs/Cargo.toml` members + deps | 移除 cli / tui / cloud-tasks / cloud-tasks-client / cloud-tasks-mock-client 五成员及 `codex-cloud-tasks-client/-mock-client/codex-tui` 三条 workspace.deps | §6.1 前门批（P1-2）：产品唯一前门 = app-server；验证 app-server 949 测试全绿（CRATES.md P1-2 行） | 2026-08-25 |
| D-005 | `codex-rs/app-server/tests/suite/v2/{mod.rs, code_mode_host.rs, executor_mcp.rs, selected_capability_stack.rs, turn_start.rs, imagegen_extension.rs}` | 删除三个测试文件 + 两个用例（均依赖已删除的 codex/cli 二进制或 code-mode-host 执行面） | AGENTS-FORK 纪律：不为已删逻辑保留测试；随 D-004 连带退役 | 2026-08-25 |
| D-006 | `codex-rs/Cargo.toml` members + deps | 移除 code-mode / code-mode-protocol / code-mode-runtime 三成员及 workspace.deps | §6.1 code-mode 手术批（CRATES.md P1-3a）：DeepSeek 单模型下执行面永不激活；连带移除 Feature/工具路由/单元格追踪等 342 处引用，workspace 不再拉 V8 | 2026-08-25 |
| D-007 | `codex-rs/Cargo.toml` members + deps | 移除 connectors / ext/connectors 两成员及 workspace.deps | §6.1 connectors 手术批（CRATES.md P1-3b）：58 处引用全部清除（ConnectorRuntimeManager 缓存通道、ConnectorSnapshot、AppToolPolicyEvaluator 内联、parse_plugin_app_config 迁入 codex-plugin、app 认证 elicitation 流程、DiscoverableTool::Connector 变体） | 2026-08-26 |
| D-008 | `codex-rs/codex-mcp/src/{rmcp_client.rs, connection_manager.rs, tool_catalog.rs, runtime.rs, lib.rs, resource_origin.rs, auth_elicitation.rs}` | 拆除 Codex Apps 工具缓存通道（McpRuntimeInput/AsyncManagedClient 字段、cached_server_info、hard_refresh 发布、类型别名）；auth_elicitation 缩为元数据键常量 | 随 D-007 连带：缓存通道仅服务 ChatGPT apps 认证，教育产品永不激活 | 2026-08-26 |
| D-009 | `codex-rs/core/src/{mcp_tool_call.rs, mcp_tool_exposure.rs, connectors.rs, session/world_state.rs, session/mod.rs, session/mcp.rs, mcp.rs, tools/spec_plan.rs}` | 移除 app 认证 elicitation 调用、apps 工具暴露分支（append_mcp_tools 简化为纯 MCP）、connectors 列表/缓存模块（仅留审批 reviewer）、Apps 使用说明 world-state 段、selected_plugin_connector_sources 死代码链、build_tool_router 的 apps_enabled 参数 | 随 D-007 连带：apps/connectors 暴露面全量清除 | 2026-08-26 |
| D-010 | `codex-rs/{tools, core-plugins, ext/mcp, ext/extension-api, plugin}` | tools 移除 DiscoverableTool::Connector 变体与 connector 校验函数；parse_plugin_app_config 迁入 codex-plugin；ext/mcp 移除 connector 声明加载与 SelectedPluginPackage.connector_ids 字段；plugin 新增 app_config 模块 | 随 D-007 连带：连接器声明/策略/加载链路迁至保留 crate 或删除 | 2026-08-26 |
| D-011 | `codex-rs/app-server/tests/suite/v2/{plugin_install.rs, recommended_plugins.rs}` | plugin_install 三个 remote-oauth 用例补回 `mount_remote_plugin_install` mock（上批删 with_apps_needing_auth 时误删）；recommended_plugins 移除 request_plugin_install 工具断言 | 修复 D-007 上批遗留红测试；request_plugin_install 工具已随 tool_suggest 裁剪 | 2026-08-26 |
| D-012 | `codex-rs/core/src/{plugins/{render.rs, render_tests.rs, injection.rs}, context/{plugin_instructions.rs, mod.rs}, session/turn.rs}` | 恢复插件显式提及能力段：`render_explicit_plugin_instructions`（去 apps 分支）、`build_plugin_injections`（去 apps 参数）、`PluginInstructions` 片段，并接回 `build_skills_and_plugins` | 修复上批过度删除：code-mode 批删 render/injection 时把普通插件（技能/MCP 服务器）的能力段一并删了，skills_extension 三个测试因此红 | 2026-08-26 |
| D-013 | `codex-rs/core/tests/suite/{cli_stream.rs, request_plugin_install.rs, mcp_turn_metadata.rs, mcp_auth_elicitation.rs}`（整文件删除）+ client/mcp_tool_exposure/openai_file_mcp/plugins/rmcp_client/codex_delegate/skills_extension 各 apps/CLI 用例 | 退役 35 个已删功能的过期测试：CLI 二进制（P1-2 删）、request_plugin_install 工具（tool_suggest 删）、apps 审批路由/apps 指南/apps 工具暴露/codex_apps 文件参数/apps 认证 elicitation 等 | AGENTS-FORK 纪律：不为已删逻辑保留测试；确定性失败（隔离亦红）经 HEAD worktree 对照确认全部指向已删功能 | 2026-08-26 |
| D-014 | `codex-rs/core/tests/suite/{agent_execution.rs, rmcp_client.rs, client.rs}` | 修三个遗留编译/断言问题：v2_residency 断言从 code-mode markdown spec 改为工具名、`McpCallEvent` 补回被误删的 derive、`ProviderAuthCommandFixture` 补回被误删的 `#[cfg(unix)]/#[cfg(windows)]` | 上批误删连带；restore 后全绿 | 2026-08-26 |

验证：`cargo check -p codex-app-server` 通过（产品二进制依赖闭包不受影响）；
app-server 测试 949 passed 全绿。已知上游自带红测试：
`environment_selection::tests::blocking_snapshot_waits_for_starting_environment`
（锚点 worktree 对照复现，非 fork 引起）。

## 上游参考途径（D10，codex/ 参考克隆已删）

1. **GitHub 在线看**：`https://github.com/openai/codex/blob/rust-v0.149.1/<path>`
2. **本地 grep**：临时克隆到仓库外任意位置（如 `~/refs/codex`）
   `gh repo clone openai/codex ~/refs/codex -- --branch rust-v0.149.1`
3. **行为对比测试**：按锚点 commit 克隆跑上游测试
   `gh repo clone openai/codex ~/refs/codex && git -C ~/refs/codex checkout ff29a44391`

跨仓 cherry-pick 配方（§2.1 铁律三）：上游克隆 `git format-patch` 出补丁 →
本仓库 `git am --directory=runtime` 应用。
