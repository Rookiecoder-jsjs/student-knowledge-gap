# Claude.md

## CodeGraph 同步规则
- 全新仓库必须在仓库根目录执行 `codegraph init` 初始化 CodeGraph 仓库。
- 每次在实际仓库根目录拉取远端最新代码后，必须执行 `codegraph sync` 更新 CodeGraph 索引，并执行 `codegraph status` 确认索引为最新状态。
- 在提交或推送代码到远端之前，必须再次执行 `codegraph sync`，确保 CodeGraph 与最终待提交代码一致。
- 如果同步产生了 Git 可追踪的 CodeGraph 文件变更，应与本次代码变更一并提交；运行时临时文件仍以仓库内 `.codegraph/.gitignore` 的规则为准，不得强制加入 Git。
