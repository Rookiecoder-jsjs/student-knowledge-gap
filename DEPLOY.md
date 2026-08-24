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

# 2) 构建并启动（backend + frontend + backup 三服务；compose 位于 deploy/ 目录）
cd deploy && docker compose up -d --build && cd ..

# 3) 访问
#    前端  : http://localhost:8080
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
| `sc-data` | `/data` | `sc.db` + `-wal`/`-shm` + `tmp/` | **唯一不可丢失状态**。WAL 模式下三文件同为在线状态 |
| `sc-backups` | `/backups` | `sc.db.<ts>.bak` | 备份产物，与 DB 卷物理隔离 |

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

## 8. 演进路径（明确不在本期交付）

- **迁 PostgreSQL**：只改 `SC_DATABASE_URL`；备份脚本换成 `pg_dump`（`scripts/backup_db.py` 已留注释）。
- **多实例**：需同时改造——`reconcile_stale` 加分布式互斥/leader 防止跨进程误判、批量临时文件改共享存储（对象存储或共享卷）、SQLite 换 PG、进程内线程池换持久化任务队列。
- **可观测增强**：`/metrics`（Prometheus）端点、LLM 主备模型 fallback。
- **鉴权/多租户**（G11）：部署形态明确后单独设计（含前端登录、全路由 `school_id` 授权）。
