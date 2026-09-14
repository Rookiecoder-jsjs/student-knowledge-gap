# runtime 裁剪记录

## 基线与恢复

本次裁剪以 `openai/codex` 的 `rust-v0.149.1` 为恢复基线，对应 commit：
`ff29a44391deccde0aba0f8390337d7f3c319ea4`。

如果裁剪后发现遗漏，可在仓库外恢复同一版本：

```powershell
git clone https://github.com/openai/codex.git ..\codex-runtime-restore
git -C ..\codex-runtime-restore checkout ff29a44391deccde0aba0f8390337d7f3c319ea4
```

然后按下表把需要的路径从 `..\codex-runtime-restore` 复制回 `runtime/`，再运行：

```powershell
codegraph sync
codegraph status
```

恢复时不要覆盖本项目已经登记在 [DELTA.md](DELTA.md) 的源码改动；先比较差异，再逐个回放。

## 本次删除范围

这些路径只服务于上游 Codex 的产品入口、开发环境、CI、SDK 或发布流程，不参与本项目当前的两个生产目标：
`//codex-rs/app-server:codex-app-server` 和 `//codex-rs/app-server:exec-server`。

| 类型 | 删除路径 | 判断 |
| --- | --- | --- |
| 上游入口/文档 | `runtime/README.md`、`runtime/CHANGELOG.md`、`runtime/docs/` | 本项目入口文档在仓库根 `README.md`，工程文档在 `handbook/` |
| 上游开发环境 | `runtime/.codex/`、`runtime/.devcontainer/` | 不参与 Bazel 生产构建 |
| 上游 Node/SDK 发布面 | `runtime/codex-cli/`、`runtime/sdk/`、`runtime/package.json`、`runtime/pnpm-lock.yaml`、`runtime/pnpm-workspace.yaml`、`runtime/.npmrc`、`runtime/.prettierignore`、`runtime/.prettierrc.toml` | 产品只直接构建 Rust app-server/exec-server |
| 上游 CI 元数据 | `runtime/.github/workflows/`、`runtime/.github/actions/`、`runtime/.github/ISSUE_TEMPLATE/` 及其 `CODEOWNERS`、`dependabot.yaml`、`blob-size-allowlist.txt`、`force` | 根仓库 `.github/` 才是本项目 CI 入口；保留 `runtime/.github/scripts/` 供 Bazel 辅助脚本使用 |
| 上游 CLI 发布脚本 | `runtime/scripts/codex_package/`、`runtime/scripts/build_codex_package.py`、`runtime/scripts/stage_npm_packages.py` | 依赖已删除的上游 CLI/SDK 发布面 |
| 上游 CLI 本地运行脚本 | `runtime/scripts/run_tui_with_exec_server.sh`、`runtime/scripts/start-codex-exec.sh`、`runtime/scripts/test-remote-env.sh` | 依赖已删除的 `codex-cli` 二进制 |
| 已退役项目 shim | `runtime/codex-rs/school-authz/` | 已从 workspace、Bazel release 和部署中移除；身份校验已迁至 backend `/mcp` 请求链 |

## 明确保留

- `runtime/codex-rs/skills/`：这是 app-server 的技能加载器和运行时内置技能，不是普通文档目录。
- `runtime/codex-rs/` 及其 Cargo/Bazel 依赖闭包：生产二进制仍从这里构建。
- `runtime/bazel/`、`runtime/patches/`、`runtime/third_party/`、`runtime/tools/`：生产 Bazel 构建仍可能读取这些目录。
- `runtime/.github/scripts/` 与 `runtime/scripts/` 中未列入删除表的脚本：保留给构建检查、格式化和 MCP 验证使用。

## 验证要求

裁剪后必须重新执行 `codegraph sync`、`codegraph status`、`git diff --check` 和
`cargo metadata --manifest-path runtime/codex-rs/Cargo.toml --no-deps --format-version 1`；推送后以根仓库 CI 的最终状态作为跨平台构建验证。
