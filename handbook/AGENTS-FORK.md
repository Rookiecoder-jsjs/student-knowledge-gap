# AGENTS-FORK —— fork 自己的工程纪律（handbook 五件套之五）

> 性质：写给在本仓库做魔改的 AI agent 与人类贡献者的工程纪律。
> 自上游 `runtime/AGENTS.md` 裁剪而来（锚点 rust-v0.149.1）：保留通用 Rust 纪律，
> 去除编码产品特定条目，新增 fork 层规则（handbook 维护 / 裁剪记账 / 铁律）。
> 与上游 AGENTS.md 的关系：上游文件随 cherry-pick 更新时，本文**人工对位**
> （上游改动不自动生效于 fork——先过一遍本文件的裁剪判断）。

## 一、fork 铁律（§2.1，违反即缺陷）

1. **runtime/ 内部零重排**：收编后目录结构一字不动；我们的代码只以
   新增 workspace 成员进入（§6.3 edu-tools / school-authz）；
2. **锚定 tag 军规**：基底一次性捐赠；上游只 cherry-pick 安全修复，
   永不整体合并；
3. **每一处源码分歧必须登记 DELTA.md**（位置/内容/原因）；账外分歧 = 缺陷；
4. **测试一律用隔离 CODEX_HOME**（如 `/tmp/sc-p1/codex-home`），
   绝不写真实 `~/.codex`。

## 二、handbook 维护纪律（§6.6）

- **文档随码走**：任何行为变更必须在同一 PR/commit 更新 handbook 对应文档；
- **裁剪记账**：每删/留一个 crate，同步更新 [CRATES.md](CRATES.md)
  （动作 + 理由 + 日期），账外增删 = 缺陷；
- **对账机制**：每批 cherry-pick 合入后抽查 ARCHITECTURE.md / DELTA.md 的
  文件路径引用是否仍成立，失效即修；
- 构建操作问题查 [BUILD.md](BUILD.md)；架构理解查 [ARCHITECTURE.md](ARCHITECTURE.md)；
  实测行为查 [FINDINGS.md](FINDINGS.md)（编号递增不复用）。

## 三、自上游保留的 Rust 纪律（摘录，全文见 runtime/AGENTS.md）

以下条目对魔改继续有效：

- crate 名前缀 `codex-`；format! 内联变量；clippy collapsible_if /
  uninlined_format_args / redundant_closure_for_method_calls 三连；
- 避免难以阅读的 bool/None 位置参数；确需时按 `argument_comment_lint`
  规范加 `/*param_name*/` 注释（`just argument-comment-lint` 本地可跑）；
- match 尽量穷尽，避免通配臂；新 trait 必须带契约 doc comment；
  不鼓励 `#[async_trait]` / `#[allow(async_fn_in_trait)]`，优先 RPITIT + Send 界；
- 测试优先整对象相等比较；不为静态值写测试；不为已删逻辑写负测试;
- 模块 <500 LoC（非机械变更 ≤800 LoC），大模块拆新文件；
- 改 `ConfigToml` 后跑 `just write-config-schema`；
- **改 Cargo.toml/Cargo.lock 必须同变更提交 MODULE.bazel.lock 更新**
  （`just bazel-lock-update`；CI 查漂移）——D8 双轨义务，详见 BUILD.md；
- `include_str!`/构建期文件读取必须同步该 crate 的 BUILD.bazel compile_data；
- MCP 工具调用相关修改优先复用 `codex-rs/codex-mcp/src/mcp_connection_manager.rs`
  既有抽象，最小化 footprint。

## 四、自上游去除的条目（魔改不适用）

- TUI 相关模块纪律（chatwidget.rs / chat_composer.rs 等）——TUI 在 §6.1 删除名单；
- 编码产品工作流条目（apply_patch 默认工具、PR 模板、release 流程的 npm 发布段）；
- 上游 docs/ 外链约定——fork 的工程文档单一归属 handbook/；
- CODEX_SANDBOX 环境变量的沙箱约定随 linux-sandbox/windows-sandbox 裁剪一并失效。

## 五、sc 侧（backend/gateway/frontend）纪律

- sc 后端四条架构不变量与「领域内核不动」原则见根 CLAUDE.md 与 backend 文档；
  Agent 化不改写业务逻辑，只经 MCP 包装暴露（§5.1）；
- gateway 是我们自己的 Python 代码：遵循 sc 后端同等风格（类型注解、
  best-effort 降级、无状态 seam）；鉴权与会话语义见设计文档 §5.5；
- prompt 资产（人格/工具描述）放 `gateway/assets/`，版本化命名（如 educator-v0），
  变更即升版本号并在 commit message 注明动机。
