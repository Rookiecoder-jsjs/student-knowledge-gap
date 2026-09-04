# 部署与运维（基础可靠 / 单机）

> 让 sc 成为**可移植、基础可靠**的常驻后端服务。定位：**单机可靠**——不丢数据、崩溃自愈、
> 依赖降级、备份可恢复。**明确不做**多实例 HA、k8s、多租户鉴权（演进方向见文末）。
> 部署形态（compose / k8s / PaaS）**后定**，故本交付物以 **Docker 容器 + docker compose** 为
> 参考部署，配置全部外部化，想部哪就部哪。

---

## 1. 快速开始

```bash
# 1) 准备环境变量（模板见 backend/.env.example；填 SC_LLM_* 才能真实调用 LLM）
cp backend/.env.example backend/.env

# 2) 构建并启动（backend + frontend + backup + gateway 四服务；compose 位于 deploy/ 目录）
#    gateway 是会话网关（agent-product-design §10.1），首次构建前置一步：
#    ① stage runtime 壳二进制（需本地代理已开，见文末「会话网关 gateway」）
#    装车批第 5 批起 gateway 自包含（独立 requirements.txt），无 backend 先建顺序。
cd deploy
./stage-gateway-runtime.sh
docker compose up -d --build && cd ..

# 3) 访问
#    前端  : http://localhost:8080
#    网关  : http://127.0.0.1:8100（Phase 1 会话网关；教师经前端登录后走 RPC/SSE）
#    后端直连: http://127.0.0.1:8000（仅本机，不对外）  Swagger: /docs
#    健康  : http://localhost:8080/healthz（nginx） /ready（就绪探针，经 nginx 透传）
```

首次使用先**导入知识库**（知识库 YAML 只负责导入，导入后 DB 即真源）：

```bash
curl -X POST http://localhost:8080/api/kb/import \
  -H 'Content-Type: application/json' \
  -d '{"yaml_path": "kb/math/grade7/kb.yaml"}'
```

> 导入的 `yaml_path` 相对容器内 `WORKDIR=/app` 解析（镜像已内置 `kb/`）。

## 2. 环境变量总表

模板 `backend/.env.example` 全量覆盖；本节只列**新增/容器化相关**项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SC_DATABASE_URL` | `sqlite:///./sc.db` | 容器内 compose 固定为 `sqlite:////data/sc.db`（卷持久化）。迁 PG 只改此项 |
| `SC_CORS_ORIGINS` | 空 | 逗号分隔允许来源；空 = 开发默认 `localhost:5173`。生产同源经 nginx，留空即可 |
| `SC_USE_ALEMBIC` | 关 | `=1` 走 `alembic upgrade head`（G10），否则 `create_all` 兜底 |
| `SC_LOG_LEVEL` | `INFO` | JSON 行日志级别（输出 stderr，容器友好） |
| `SC_BACKUP_INTERVAL_HOURS` | `24` | 热备间隔小时 |
| `SC_BACKUP_KEEP` | `30` | 保留备份份数 |
| `SC_BACKUP_DIR` | `/backups` | 备份落盘目录（compose 内为命名卷 `sc-backups`） |
| `SC_LLM_AUDIT` | 开 | LLM 调用全程审计（`llm_call_log` 表，append-only）；`=0` 关闭 |
| `SC_LLM_AUDIT_PAYLOAD` | 关 | `=1` 时审计额外存响应 JSON（调试用；输入原文任何情况不落库） |
| `TMPDIR` | `/data/tmp` | **compose 注入**：批量上传临时文件挂卷，重启后 failed item 可重试 |

其余算法/质量参数（`SC_MIN_EVIDENCE_COUNT`、`SC_WEAKNESS_MODE` 等）见 `.env.example` 与 README，不改默认即用生产已转正的取值。

## 3. 数据与卷

| 卷 | 挂载点 | 内容 | 说明 |
|---|---|---|---|
| `sc-data` | `/data`（backend、backup） | `sc.db` + `-wal`/`-shm` + `tmp/` | **唯一不可丢失状态**。WAL 模式下三文件同为在线状态。装车批第 5 批起 **gateway 不再挂此卷**——sc MCP 已迁入 backend 进程，agent 物理不可达 sc.db |
| `sc-backups` | `/backups` | `sc.db.<ts>.bak` | 备份产物，与 DB 卷物理隔离 |
| `gw-codex-home` | `/data/codex-home` | CODEX_HOME **根**（含 `threads.json`）；每个教师驱动一个 `t<teacher_id>/` 子目录 = 其 config.toml/models.json + rollout（装车批第 6 批起） | 壳侧持久状态；按驱动惰性播种、缺省不覆盖；`threads.json` 落卷内，容器重建不丢 |

- `kb/`（kb.yaml）只是**导入源**，导入后 DB 才是知识库真源；改图谱走 `/kb` 编辑或重新导入。
- `output/` 仅脚本演示产物，不入卷、不参与运行时。

## 4. 备份与恢复

**机制**：`backup` 服务复用后端镜像跑 `backup_loop.sh`——每 `SC_BACKUP_INTERVAL_HOURS`（默认 24h）
调 `python -m scripts.backup_db`（SQLite backup API **在线热备**，WAL 下不阻塞读写），保留最近
`SC_BACKUP_KEEP`（默认 30）份。备份只读源库、写独立 `.bak`，**不构成第二个写进程**（守住单写者不变量）。
失败不退出循环，下一轮自愈。

手动触发一轮备份（以下 `docker compose` 命令均在 `deploy/` 目录内执行）：

```bash
docker compose exec backup python -m scripts.backup_db /backups/manual.bak
docker compose exec backup ls -1 /backups        # 查看备份列表
```

**恢复三步**（以某份 `.bak` 还原到备份点，RPO = 一个备份间隔，可调小 `SC_BACKUP_INTERVAL_HOURS`）：

```bash
# 1) 停 backend（避免写者与还原争用）
docker compose stop backend
# 2) 用 .bak 覆盖 sc.db，并删除 WAL/SHM（否则旧 WAL 会回放覆盖还原内容）
docker compose run --rm backup sh -c 'cp /backups/sc.db.<最新时间戳>.bak /data/sc.db && rm -f /data/sc.db-wal /data/sc.db-shm'
# 3) 拉起并验证
docker compose start backend
curl -s localhost:8080/ready
```

**恢复演练**（建议每季度做一次）：先在测试环境按上述三步还原，确认数据回到备份点、业务可读。

## 5. 单进程原理（必须守住）

后端**必须单 uvicorn 进程**（`--workers 1`，不用 gunicorn）。原因是四个架构事实：

1. **批量录卷**用进程内 `ThreadPoolExecutor`（`batch.py`）——任务只在收到请求的那个进程里执行；
2. **启动期 `reconcile_stale()` 全局改判**（`main.py`）——若多 worker 各自启动，会误杀**别的 worker 正在处理**的 item；
3. **熔断器是进程内单例**（`llm/circuit.py`）——多进程各持一个，熔断语义分裂；
4. **SQLite 单写者调优**（WAL + 15s busy_timeout）——多进程写会 `database is locked`。

并发需求（一批 40-50 张卷的解析）由**线程**承担（`SC_BATCH_WORKERS`），不靠多进程。崩溃自愈由
宿主 `restart: unless-stopped` 提供。**想突破单进程，必须先做文末「演进路径」的改造**，否则会踩上述四个坑。

> 装车批第 5 批起，sc MCP server 已**迁入本进程**：`backend/app/mcp_server.py` 的 FastMCP
> 实例经 streamable-http 挂 `/mcp`（`app/mcp_http.py`），与 REST 共用同一 DB engine——**「第二
> SQLite 访问者」例外消除**，单写者不变量回归纯粹。codex 壳在 gateway 容器经网络连 `/mcp`，
> 不再有任何进程跨容器读 sc.db。

## 6. 健康与可观测

| 探针 | 路径 | 语义 |
|---|---|---|
| liveness | `GET /health`（backend）、`GET /healthz`（nginx） | 进程 + HTTP 存活 |
| readiness | `GET /ready`（backend，经 nginx `/ready` 透传） | DB 可达 = 200；**LLM 熔断 = 仍 200 但 `degraded:true`**（确定性路径不依赖 LLM）；DB 不可达 = 503 |

- compose 的 `backend` healthcheck 打 `/ready`，DB 不可达 → 不健康 → 触发重启自愈。
- 日志为 **JSON 行 → stderr**（`docker compose logs -f` 直接可读），聚合器可直接摄取。
- LLM 断供时：Excel 导入、推导、报告模板等确定性路径照常工作；仅拍照解析/报告 AI 解读段受影响。

## 7. 常见运维（均在 `deploy/` 目录内执行）

```bash
docker compose ps                            # 三服务状态（backend 应 healthy）
docker compose logs -f backend               # 后端结构化日志
docker compose restart backend               # 手动重启（数据在卷中，安全）
docker compose build && docker compose up -d # 升级（含前端重建）
```

## 8. 会话网关 gateway（Phase 1 §10.1；装车批第 3 步 = runtime 魔改壳，第 5 步 = 容器级隔离）

四服务之一：浏览器侧教师经网关（鉴权 RPC + SSE）连到 codex 壳；网关每教师 spawn 一个
`codex-app-server` 子进程（stdio JSON-RPC）。壳经 `[mcp_servers.sc]`（CODEX_HOME
config.toml）以**远程 streamable-http** 调 sc 域工具——sc MCP 迁入 backend 进程（/mcp），
教师身份逐请求经 `Authorization: Bearer`（网关按教师签发的 HMAC token）由 backend 验签；
**gateway 容器不挂 sc-data 卷**，agent 壳物理不可达 sc.db（装车批第 5 批的收紧）。

- **镜像**：`gateway/Dockerfile`——python:3.11-slim + 自包含依赖（`gateway/requirements.txt`，
  不再 COPY backend） + runtime 壳二进制（codex-app-server/exec-server）。
- **构建前置**：`deploy/stage-gateway-runtime.sh`（容器化 bazel 出两二进制到
  gateway/.runtime，需本地代理）。无 backend 先建顺序。镜像不构建于 CI（CI docker job
  只 build backend/frontend + compose config）。
- **env**：`env_file: ../backend/.env`（网关**进程**侧要 SC_AUTH_SECRET 签教师 token、
  SC_TRIGGER_KEY 验 /internal/*、SC_LLM_API_KEY 供播种取 key）；`environment` 覆盖
  `SC_GATEWAY_APP_SERVER=codex-app-server`（无子命令，args 空）。**agent 子进程 env 是
  白名单**（`gateway/main.py _child_env`）——共享密钥/DB URL/LLM key 一概不进，防注入 agent
  `env` 外泄。
- **CODEX_HOME 按驱动分（装车批第 6 批）**：`/data/codex-home` 是**根**；每个驱动（教师
  身份）用其下 `t<teacher_id>/` 作 codex home。`Bridge.spawn` 前惰性播种该 home（
  `gateway/codex_home.py` 渲染 `assets/deepseek` 模板：DeepSeek key = `SC_DEEPSEEK_API_KEY`
  回落 `SC_LLM_API_KEY`；`[mcp_servers.sc]` 远程 url + **条件** bearer_token_env_var——
  SC_AUTH_SECRET 配置（安全模式）才渲染该键行，未配（开放模式）整键省略 = codex 匿名
  （codex 对「引用到未设 env 的 bearer 键」fail-closed 拒启 sc MCP，省略是匿名唯一形态）+
  落 models.json；**模式翻转不自愈**：改配 SC_AUTH_SECRET 后需删 `t<tid>/config.toml`
  让下次 spawn 重播种；
  管理员手写永不覆盖，旧 stdio 形（含 school-authz-mcp）自动旋转 `.pre-mcp-remote.bak`
  重渲染。**装车批第 7 批 per-teacher UID**：home 及其内容收归教师专用 uid（
  `20000+teacher_id`，目录 0700/文件 0600），`Bridge.spawn` 经 `setpriv --reuid` 降权
  启动 codex（`HOME`/`TMPDIR` 一并收进驱动 home；非 root 开发环境裸启降级 + 大声告警）；
  CODEX_HOME 根 0711（可穿越不可列，隐藏同级教师存在）、`threads.json` 0600（仅网关
  可读）——跨教师读 rollout/config/进程 env 由内核拒绝（§8 已知限制 #1 文件/进程面
  关闭）。升级路径：第 6 批既有根属主内容在首次 spawn 时递归收编。
  **持久线程映射** `threads.json`（键 `class_id.teacher_id`，见下）落卷内根下——
  容器重建不丢。
- **触发式持久线程**（§5.6）：`threads.json` 键 = `"{class_id}.{teacher_id}"`（`main.py
  _thread_key`）——按教师分 home 后两教师不共享一类线程，班主教师与系统（同教师身份
  trigger）以同一键寻址、同 home 可 resume；不同教师互不越界。**升级说明**：自第 5 批
  旧布局升级，旧根级 sessions/config 原地不动（drivers 只用 `t*/`）、旧裸 `class_id`
  键不再命中——持久线程记忆随键改版重置（试点未装机，接受；retention 保留根级兜底扫）。
- **教师账号**：`gateway/accounts.example.json` 模板 → `python scripts/gateway_account.py`
  生成带 teacher_id 的 accounts.json（入库于镜像 accounts.json 兜底位）。
- 端口 `127.0.0.1:8100`（仅本机调试/健康；浏览器侧走前端 nginx 反代，见 compose 注释）。

### 已知限制 / 残留风险（装车批第 5 批显式记账）

1. **跨教师文件/进程读取已内核级关闭（装车批第 7 批）**：每教师驱动以专用 uid
   （`20000+teacher_id`）运行、home 0700/文件 0600、`setpriv` 降权 spawn——注入 agent
   读同级教师 rollout/config、读他人 `/proc/*/environ`、ptrace 他人进程，均被内核拒绝
   （容器 e2e 实证）；CODEX_HOME 根 0711 连同级教师目录的存在性都不可列。**仍共享的**：
   网络面（同 netns）——agent 可达 backend:8000 与外网：security 模式下 /mcp 无有效
   教师 token 即 401；开放模式下匿名放行属开放模式的定义（residual #2/#3 照旧）；/tmp
   （1777）跨 uid 可写粘滞共享——敏感默认落点已收编进 0700 的 HOME/TMPDIR，agent 主动
   外传数据等同网络面残留（主动 exfiltration,非被动可读）。降级路径：非 root 运行网关
   （开发环境）或镜像缺 setpriv 时裸启 + 告警,边界退化为第 6 批目录纪律——生产镜像自带
   setpriv（util-linux），root 容器为唯一受支持部署形态。
2. **DeepSeek key 在 agent 进程内**：codex 自调 LLM，config.toml 明文持 provider key——
   注入的 agent 可外泄；按设计残留（agent 必须能调模型）。
3. **agent 可达 backend:8000**：REST 全部 Bearer 门；子进程已无 SC_AUTH_SECRET/SC_TRIGGER_KEY，
   不可伪造教师或内部 token；/mcp 本身逐请求验签。
4. **自动 trigger 按提交教师身份驱动**：commit 实名教师带入 teacher_id，安全模式下自动考后
   分析可读本班数据（第 5 批修复的既有缺口）；开放模式无教师时回落匿名。
5. **token TTL = 7 天**：逐请求重验的必然（对齐 backend auth.py）；gateway 重部署即重签。

## 9. 演进路径（明确不在本期交付）

- **迁 PostgreSQL**：只改 `SC_DATABASE_URL`；备份脚本换成 `pg_dump`（`scripts/backup_db.py` 已留注释）。
- **多实例**：需同时改造——`reconcile_stale` 加分布式互斥/leader 防止跨进程误判、批量临时文件改共享存储（对象存储或共享卷）、SQLite 换 PG、进程内线程池换持久化任务队列。
- **可观测增强**：`/metrics`（Prometheus）端点、LLM 主备模型 fallback。
- **鉴权/多租户**（G11）：部署形态明确后单独设计（含前端登录、全路由 `school_id` 授权）。
