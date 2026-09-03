# AGENTS.md

## CodeGraph 同步规则
- 全新仓库必须在仓库根目录执行 `codegraph init` 初始化 CodeGraph 仓库。
- 每次在实际仓库根目录拉取远端最新代码后，必须执行 `codegraph sync` 更新 CodeGraph 索引，并执行 `codegraph status` 确认索引为最新状态。
- 在提交或推送代码到远端之前，必须再次执行 `codegraph sync`，确保 CodeGraph 与最终待提交代码一致。
- 如果同步产生了 Git 可追踪的 CodeGraph 文件变更，应与本次代码变更一并提交；运行时临时文件仍以仓库内 `.codegraph/.gitignore` 的规则为准，不得强制加入 Git。

## 推送与 CI 关注
- 每次推送到远端后，必须定位本次推送对应的 CI run（`gh run list --branch main` 按 headSha 匹配，或 `gh run view <id>`）并关注至终态：结论 success 才视为推送完成；失败则查 job 定位原因、修复后再推送。上报时说明 CI 覆盖面边界（哪些 job 不跑、正确性靠本地验证兜底），不得把本地测试当 CI 结果上报。
