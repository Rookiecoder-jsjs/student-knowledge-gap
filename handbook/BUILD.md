# BUILD —— 构建与测试指南（handbook 五件套之四）

> 性质：魔改仓库的构建/测试/发布操作手册。上游仓内无对应单篇（散落在 AGENTS.md
> 与 GitHub workflow 注释），本文是 fork 自有工程知识的唯一归属（§6.6）。
> 锚点：rust-v0.149.1（见 [DELTA.md](DELTA.md) 头部）。

## 平台支持矩阵

| 场景 | 平台 | 说明 |
|---|---|---|
| **生产** | linux（docker 容器，arm64/amd64） | compose 单盒部署；gateway 镜像内置预编译壳 |
| **开发** | macOS arm64 | 日常魔改与测试环境 |
| CI | 精简为单平台矩阵 | 上游多平台矩阵不随 fork 维护；Bazel 远程构建等重特性停用 |

## 双构建系统（D8 已定：Cargo + Bazel 双轨保留）

仓库同时维护两套构建，同步义务四条（违反任何一条 = 缺陷）：

1. **新增 crate 必须配 `BUILD.bazel`**——照抄同目录邻近 crate 的目标结构；
2. **改依赖必须更新 lockfile 并同变更提交**：`just bazel-lock-update`
   （MODULE.bazel.lock 约 1.5MB，随 fork 演进维护）；
3. **CI 保留漂移检查**：`bazel-lock-check` 防止 lockfile 过期；
4. **cherry-pick 不挑半边**：上游补丁带 `BUILD.bazel` 变更时照常合入
   （跨仓配方见 DELTA.md「上游参考途径」）。

## 常用命令

所有命令在仓库根执行（`runtime/justfile` 已设 `working-directory := codex-rs`）：

```bash
# ---- Cargo 轨（日常开发主力）----
cargo build -p codex-app-server        # 壳主二进制（网关对接面）
cargo test -p <crate>                  # 单 crate 测试
just test                              # 全 workspace 测试（nextest，需 cargo install cargo-nextest）
just fmt                               # Rust + Bazel + Python 全量格式化
just fmt-check                         # 只查不改

# ---- Bazel 轨（双轨义务 + 发布构建）----
bazel test //codex-rs/<crate>:all      # 单 crate 的 bazel 测试
just bazel-test                        # 全量（排除 argument-comment-lint tag）
just bazel-clippy                      # clippy 经 bazel（CI 用）
just bazel-lock-update                 # 依赖变更后必跑（义务②）

# ---- 发布 ----
just build-for-release                 # bazel 构建 release 二进制
```

## 本地运行壳（对接 sc MCP）

```bash
# 隔离 CODEX_HOME（铁律：绝不写真实 ~/.codex，见 FINDINGS F8 与 memory 规则）
export CODEX_HOME=/tmp/sc-p1/codex-home
# 该目录需含 config.toml（DeepSeek provider + mcp_servers.sc）与 models.json
# 模板来源：gateway/assets/deepseek/

cargo run -p codex-cli --bin codex -- exec --skip-git-repo-check "查询班级概览"
```

## 发布产物形态（一校一盒 §8）

- **backend/frontend/backup**：既有镜像不变；
- **gateway**：`gateway/Dockerfile`——node22-slim + codex npm 预编译二进制
  （与锚点同版本）+ FastAPI 网关 + assets 分发（DeepSeek 模板 / 人格 prompt）。
  Phase 1 裁剪完成后，切换为 runtime/ 源码构建的魔改壳（届时改 Dockerfile 安装段，
  多阶段构建：`just build-for-release` 产物 COPY 进 slim 运行层）；
- CODEX_HOME 卷持久化 rollout 与线程记忆（§5.6 一班一线程）。

## 测试纪律（fork 版）

- 上游测试全量随锚点保留；裁剪一个 crate 时**连同其测试目标一起移除**
  （CRATES.md 记账），不允许留孤儿测试引用；
- 魔改行为的新增测试放对应 crate 内（Rust 侧）；sc 侧业务回归在 backend/tests；
- 每批 cherry-pick 合入后跑 `just fmt-check && just test`，再按 §6.6 对账机制
  抽查 handbook 路径引用。
