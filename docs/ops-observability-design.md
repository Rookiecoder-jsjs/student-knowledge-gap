# 运维可观测性设计

## 1. 目标与边界

知行教研当前以 Docker Compose、单机单实例为主，运维侧首先要解决三件事：

1. 出错后能在日志中定位请求、组件、任务和依赖；
2. 服务异常能被探针和指标及时发现，而不是等用户反馈；
3. 值班人员有一套短路径的排障与恢复动作。

本方案保持单机部署简单：默认只依赖容器标准输出、健康探针和 Prometheus 兼容文本指标；生产环境可以把这些输出接入已有的日志/监控平台。当前阶段不新增错误日志数据库、不引入 OpenTelemetry、Kafka、ELK 或 Kubernetes。

## 2. 当前能力与缺口

已有能力：

- `backend/app/observability.py` 已将 `sc.*` 日志输出为 JSON 行到 stderr；
- backend 有 `/health`、`/ready`、`/metrics`，Compose 通过 `/ready` 做健康检查；
- backend 请求已有 `X-Request-ID`、请求耗时和基础计数器；
- LLM 调用有 `llm_call_log` 审计表，异步任务和批量录入会保留失败状态；
- gateway 有每日出站心跳，包含版本、ready、磁盘和桥接进程状态；
- backup 服务有独立备份卷和恢复流程。
- backend 已有全局异常/参数校验处理：对外返回安全错误码和 `request_id`，堆栈只写受控日志；
- JSON 日志自动补 `service`、`version`，并过滤 password/token/secret/prompt 等敏感字段；
- gateway 的生产路径已统一为结构化 logger；Compose 服务已配置本地日志轮转（20MB × 5）。

主要缺口：

- 指标仍主要是进程内计数器，尚未覆盖延迟直方图、任务积压、备份和磁盘水位；
- gateway 尚未暴露独立 Prometheus 指标，心跳仍是摘要上报；
- 告警规则模板和集中日志接入尚未提供，需要接入学校或运营侧现有平台；
- 备份恢复已有手册，但故障演练尚未自动化。

## 3. 日志设计

### 3.1 输出与保留

所有服务统一输出到 stdout/stderr，由容器运行时或宿主采集，不在应用中自行写滚动文件。Compose 默认使用本地日志驱动并限制单文件大小，例如：

```yaml
logging:
  driver: local
  options:
    max-size: "20m"
    max-file: "5"
```

单机默认保留约 100MB/服务；接入 Loki、云日志或 journald 时由外部系统负责更长留存。日志轮转不能替代业务数据备份。

### 3.2 统一事件字段

每条结构化日志至少包含：

```json
{
  "ts": "2026-09-14T08:00:00.000Z",
  "level": "ERROR",
  "service": "backend",
  "version": "2026.09.14-abc123",
  "logger": "sc.http",
  "event": "http.request_failed",
  "message": "request failed",
  "request_id": "uuid",
  "method": "POST",
  "route": "/exams/{exam_id}/submit",
  "status_code": 500,
  "duration_ms": 183.4,
  "error_type": "IntegrityError",
  "error_code": "internal_error",
  "retryable": false
}
```

按场景追加 `job_id`、`item_id`、`exam_id`、`capability`、`provider`、`attempt`、`dependency` 等有限字段。`route` 必须使用路由模板，不记录带 ID 的原始 URL，避免高基数。

禁止写入：密码、Bearer/API token、Cookie、完整请求体、学生答卷原文、LLM prompt/response 原文、邮箱和手机号。用户名也只在鉴权失败计数场景使用不可逆摘要或直接省略。

### 3.3 必须统一的事件

| 事件 | 级别 | 说明 |
|---|---|---|
| `http.request_completed` | INFO | 所有业务请求（探针 `/health` `/ready` `/metrics` 除外——探针计入指标、不占日志配额），含 request_id、route、状态码、耗时 |
| `http.request_failed` | ERROR | 未处理异常或 5xx，带异常类型和堆栈 |
| `auth.login_rejected` | INFO/WARN | 401、429 只记录原因码，不记录密码 |
| `job.failed` | ERROR | 后台任务最终失败，带 job_id 和可重试性 |
| `batch.item_failed` | ERROR | 单个上传项失败，带 item_id 和阶段 |
| `llm.call_failed` | WARN/ERROR | 供应商、能力、熔断状态和耗时，不记录正文 |
| `dependency.health_changed` | WARN | DB、Redis、LLM router 状态变化；`/ready` 探针轮询不逐次告警——同一故障签名只记一条，每 60s 重报心跳，恢复时记一条 INFO |
| `backup.completed` / `backup.failed` | INFO/ERROR | 备份路径、耗时、大小和结果 |
| `heartbeat.completed` / `heartbeat.failed` | INFO/WARN | 只记录目标域名、状态码和下次重试 |

后台任务和网关事件必须复用同一套 JSON formatter；删除 gateway 中面向生产路径的 `print`，改为标准 logger。

## 4. 错误处理与关联

### 4.1 Backend

增加一个全局异常处理器，行为固定为：

- 生成或复用 `request_id`；
- 记录一次 `http.request_failed`，含完整堆栈和安全字段（`http_errors_total` 计数由请求中间件的 finally 统一负责，异常处理器不重复计数）；
- 对外只返回 `{"detail":"服务内部错误","request_id":"..."}`，不返回 SQL、文件路径、供应商响应或环境变量；
- 5xx 统一增加 `X-Request-ID`，便于用户把页面错误交给运维定位；
- 已知业务异常继续由各路由返回 4xx，不重复记录为内部错误。

`/ready` 的响应只返回稳定的原因码，例如 `database_unavailable`、`redis_unavailable`，底层异常文本只进入受控日志。`/health` 保持无依赖 liveness，不因数据库或 LLM 故障变成 500。

### 4.2 Gateway

网关请求、SSE 断开、子进程退出、后端不可达、心跳失败都使用同样的 `request_id`/`bridge_id` 字段。子进程 stderr 只截取长度受限的摘要，禁止原样转发可能包含 prompt 或密钥的内容。

## 5. 指标设计

`/metrics` 继续使用 Prometheus exposition 格式。当前进程内计数器保留作为无依赖默认，但应补齐以下有限维度；不使用 teacher_id、student_id、原始 URL 等高基数标签。

### 5.1 Backend 指标

| 指标 | 类型 | 关键标签/用途 |
|---|---|---|
| `sc_http_requests_total` | counter | method、route、status_class |
| `sc_http_request_duration_ms` | histogram | method、route；计算 P50/P95/P99 |
| `sc_http_errors_total` | counter | route、error_code |
| `sc_auth_failures_total` | counter | reason、status_class |
| `sc_db_errors_total` | counter | operation |
| `sc_llm_calls_total` | counter | capability、provider、status |
| `sc_llm_call_duration_ms` | histogram | capability、provider |
| `sc_llm_circuit_open` | gauge | capability |
| `sc_jobs_pending` / `sc_jobs_failed_total` | gauge/counter | job kind |
| `sc_batch_items_failed_total` | counter | stage |
| `sc_backup_last_success_timestamp` | gauge | 最近成功时间 |
| `sc_backup_failures_total` | counter | 失败次数 |
| `sc_process_resident_memory_bytes` | gauge | 进程内存水位 |

### 5.2 Gateway 与心跳

网关增加：`sc_gateway_requests_total`、`sc_gateway_request_duration_ms`、`sc_gateway_bridges`、`sc_gateway_subprocess_exits_total`、`sc_gateway_backend_ready`、`sc_gateway_sse_disconnects_total`。每日心跳继续只上报摘要，不上报业务日志；可以增加最近一次备份时间、错误计数和磁盘剩余比例。

### 5.3 指标持久性

单机模式接受进程重启后计数器清零，告警使用速率、时间戳和连续失败次数，不依赖单调递增总量永久保存。需要历史趋势时由 Prometheus/云监控抓取；不把指标写入业务数据库。

## 6. 探针与告警

### 6.1 探针语义

| 探针 | 成功条件 | 失败动作 |
|---|---|---|
| `/health` | 进程能响应 HTTP | 容器重启 |
| `/ready` | DB 可用；HA 时 Redis 可用 | 摘流量/容器自愈；LLM 熔断只标 `degraded` |
| `/metrics` | 可抓取指标 | 监控端报警，不触发应用重启 |
| gateway `/health` | 网关进程与桥接状态可读 | 网关重启或人工检查；backend 状态单独展示 |

### 6.2 第一版告警规则

| 优先级 | 条件 | 建议动作 |
|---|---|---|
| P1 | `/ready` 连续 3 次失败或 backend 容器不 healthy | 先看 DB/Redis，再执行容器重启 |
| P1 | 5xx 比例 > 2% 且 5 分钟请求数 ≥ 20 | 按 request_id 查 ERROR 日志 |
| P1 | 数据库错误持续 2 分钟 | 停止写入型操作，检查磁盘/锁/连接 |
| P2 | LLM 失败率 > 20% 持续 10 分钟或熔断打开 | 检查 router、供应商额度；确认确定性路径仍可用 |
| P2 | 任务最终失败 ≥ 3 次/10 分钟或最老任务 > 10 分钟 | 按 job_id 排查，必要时暂停队列 |
| P2 | 最近一次备份超过 26 小时或连续备份失败 | 立即手动备份并检查备份卷 |
| P2 | 磁盘剩余 < 20%，< 10% 为严重 | 清理日志/临时文件，确认备份卷容量 |
| P3 | 心跳超过 26 小时未收到 | 联系学校或检查盒子网络/容器状态 |
| P3 | 登录 429 短时突增 | 检查误配置、暴力尝试或前端重试循环 |

告警必须带服务、版本、最近 request_id/job_id 和操作链接；告警恢复也要通知，避免只报不收敛。

## 7. 最小落地批次

### 批次 A：应用侧基础闭环（已落地）

1. 全局异常处理器和安全错误响应；
2. 统一 `event`、`service`、`version`、`request_id` 字段；
3. `/ready` 改为原因码，实际异常只进日志；
4. Compose 为 backend、gateway、backup、llm-router 配置日志轮转；
5. gateway 去除生产路径 `print`。

### 批次 B：指标与运维入口（部分落地）

1. 已将请求计数和耗时计数扩展为有限标签，并增加依赖 ready gauge；
2. 已补 `/metrics`、安全错误响应和 readiness 失败场景的自动化测试；
3. 待补任务、备份、磁盘、网关桥接指标和延迟直方图；
4. 待提供可选的 Prometheus scrape 配置，不把 Prometheus 作为默认运行服务。

### 批次 C：告警与演练

1. 提供 Prometheus 告警规则模板；
2. 编写“服务不可用、DB 故障、LLM 断供、备份恢复、磁盘告警”五个演练步骤；
3. 每季度在测试环境做一次恢复演练，并记录 RPO/RTO；
4. 规模化部署后再决定是否接入集中日志和心跳接收端。

## 8. 明确不做的事情

- 不为每条错误新增业务数据库表；日志检索由容器运行时或外部日志系统负责；
- 不把完整请求体、学生数据和 LLM 内容写进日志；
- 不使用 teacher_id、student_id、原始 URL 作为 Prometheus 标签；
- 不因为 LLM 暂时不可用就让 `/health` 失败或重启整个 backend；
- 在没有多实例和历史查询需求前，不引入分布式 tracing、Kafka、ELK 或 Kubernetes。

## 9. 验收标准

- 任意一次 500 都能用响应头 `X-Request-ID` 在 JSON 日志中定位；
- 日志中搜索不到密码、Bearer token、Cookie、LLM prompt/response 原文；
- `/ready` 对外不泄露 SQL/Redis 原始错误；
- 重启后服务能自愈，监控通过时间窗口和备份时间戳继续报警；
- 手动故障演练能在 10 分钟内判断是应用、数据库、LLM、磁盘还是网关问题；
- 默认单机部署不新增必须维护的监控服务。
