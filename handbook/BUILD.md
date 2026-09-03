# BUILD —— 构建与测试指南（handbook 五件套之四）

> 性质：魔改仓库的构建/测试/发布操作手册。上游仓内无对应单篇（散落在 AGENTS.md
> 与 GitHub workflow 注释），本文是 fork 自有工程知识的唯一归属（§6.6）。
> 锚点：rust-v0.149.1（见 [DELTA.md](DELTA.md) 头部）。

## 平台支持矩阵

| 场景 | 平台 | 说明 |
|---|---|---|
| **生产** | linux（docker 容器，amd64 已验） | compose 单盒部署；gateway 镜像内置 runtime 魔改壳二进制（装车批第 3 步后） |
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
# 装车批第 5 批起 [mcp_servers.sc] 是远程 url（http://backend:8000/mcp，compose 内
# 服务名）——本地非 compose 跑壳时，把该 url 改成 http://127.0.0.1:8000/mcp 并本地起
# backend（uvicorn app.main:app，已挂 /mcp）；身份：SC_AUTH_SECRET 配置时给子进程
# SC_SCHOOL_AUTH_TOKEN（backend issue_token 同格式签发），否则开放模式匿名。

cargo run -p codex-cli --bin codex -- exec --skip-git-repo-check "查询班级概览"
```

## 发布产物形态（一校一盒 §8；装车批第 3 步：gateway 换装 runtime 魔改壳；第 5 步：自包含 + 容器级隔离）

- **backend/frontend/backup**：既有镜像不变；
- **gateway**：`gateway/Dockerfile`——python:3.11-slim 基 + **自包含依赖**
  （`gateway/requirements.txt`，装车批第 5 步起不再 `COPY --from=sc-backend`，
  无 backend 先建顺序），COPY runtime Bazel 产物（`codex-app-server`/`exec-server`）
  进 `/usr/local/bin/`。sc MCP server 已迁入 backend 进程（/mcp），gateway 不携带
  backend python、**不挂 sc-data 卷**——agent 物理不可达 sc.db。构建：
  ```bash
  # 1) stage runtime 壳二进制（本机无 bazel，走容器；需先开代理）→ 两枚
  deploy/stage-gateway-runtime.sh
  # 2) 构建（自包含，无顺序）
  docker compose -f deploy/docker-compose.yml build gateway
  ```
- `just build-for-release` 产物 = `codex-app-server` + `exec-server` +
  `school-authz-mcp`（school-authz-mcp 第 5 批起**不再 stage 进镜像**——crate 保留
  作参考实现，见 §6.3）；
- **CODEX_HOME 按驱动分 + 惰性播种**（装车批第 6 批）：`$SC_GATEWAY_CODEX_HOME` 是**根**，
  每教师驱动用其下 `t<teacher_id>/`；`Bridge.spawn` 前 `main.py _seed_driver_home` 对驱动
  home 渲染（`[mcp_servers.sc]` = 远程 `url=http://backend:8000/mcp` +
  `bearer_token_env_var=SC_SCHOOL_AUTH_TOKEN`）+ 落 models.json；幂等，管理员手写永不覆盖；
  旧 stdio 形（含 school-authz-mcp）配置自动旋转 `.pre-mcp-remote.bak` 重渲染；
- CODEX_HOME 卷持久化 rollout 与线程记忆（§5.6 一班一线程）：持久线程映射 `threads.json`
  键 = `class_id.teacher_id`（`main.py _thread_key`），落卷内根下、容器重建不丢；同教师
  跨 bridge 重建 resume，不同教师互不越界（目录级隔离，注入 agent 越级读仍可——DEPLOY §8）。
- **arch**：staged 二进制 = x86_64-linux-gnu（glibc ≥2.28）；arm64 盒子需在 arm
  环境重跑 staging。
- **已知限制（版本 stamp 暂缓）**：rules_rs 0.0.96 的 cargo-bazel 不解析 workspace
  继承版本（`version.workspace = true`），bazel 轨产物 `CARGO_PKG_VERSION` 恒
  `0.0.0` → `codex-app-server --version` / `cli_version` / otel 字段为 0.0.0
  （npm 壳为 cargo 构建故 0.149.1）。sc 前端/网关不展示 codex 版本，非用户可见；
  修复需 defs.bzl rust_binary `rustc_env` 补 `CARGO_PKG_VERSION`（fork 分歧账），
  待真正需要展示壳版本时再做。

### §6.3 新增：school-authz（身份校验 + 注入）

```bash
# Cargo 轨
cargo check -p codex-school-authz
cargo test -p codex-school-authz        # RUST_MIN_STACK=8388608

# Bazel 轨（发布用；新增 crate 必须配 BUILD.bazel——D8 义务）
bazel build //codex-rs/school-authz:school-authz-mcp
bazel test //codex-rs/school-authz:all

# 依赖变更后同步 lockfile（义务②）
just bazel-lock-update
```

产物 `school-authz-mcp` = stdio MCP shim（Rust HMAC 验签，决策表 Passthrough/
Anonymous/SetTeacher/FailClosed）。**部署已退役（装车批第 5 批）**：sc MCP 迁入 backend
进程后 `[mcp_servers.sc]` 为远程 url，url 与 command 互斥、shim 不再被 spawn，也不再
stage 进 gateway 镜像。其校验职责**迁往 backend 逐请求**（`auth.verify_token` 同格式同
密钥，`app/mcp_http.py` 中间件）；crate + 测试**保留在树内作参考实现**（token 格式与
裁决表的唯一 Rust 侧记录），上方命令作能力文档。生产教师身份链路（第 5 批）：gateway 按
教师签 token → codex MCP client 逐请求 `Authorization: Bearer` → backend /mcp 验签 →
`auth.mcp_context` 按教师/班级过滤。逐调用教师↔班级断言由 sc 后端执行（不变）。

## 测试纪律（fork 版）

- 上游测试全量随锚点保留；裁剪一个 crate 时**连同其测试目标一起移除**
  （CRATES.md 记账），不允许留孤儿测试引用；
- 魔改行为的新增测试放对应 crate 内（Rust 侧）；sc 侧业务回归在 backend/tests；
- 每批 cherry-pick 合入后跑 `just fmt-check && just test`，再按 §6.6 对账机制
  抽查 handbook 路径引用。
