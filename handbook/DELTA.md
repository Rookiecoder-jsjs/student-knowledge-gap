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

*当前为空——Phase 0 为零源码修改验链（设计文档 §10.1），收编即锚点原样。*

Phase 1 首笔修改（预计：基础 system prompt 替换 / 默认工具面退场）起开账。

## 上游参考途径（D10，codex/ 参考克隆已删）

1. **GitHub 在线看**：`https://github.com/openai/codex/blob/rust-v0.149.1/<path>`
2. **本地 grep**：临时克隆到仓库外任意位置（如 `~/refs/codex`）
   `gh repo clone openai/codex ~/refs/codex -- --branch rust-v0.149.1`
3. **行为对比测试**：按锚点 commit 克隆跑上游测试
   `gh repo clone openai/codex ~/refs/codex && git -C ~/refs/codex checkout ff29a44391`

跨仓 cherry-pick 配方（§2.1 铁律三）：上游克隆 `git format-patch` 出补丁 →
本仓库 `git am --directory=runtime` 应用。
