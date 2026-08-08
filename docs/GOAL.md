# 运行时可用性与扩展性改进 - 目标文档 v0.1

> 状态：待评审 · 所在目录：项目根（与 `improvement-plan.md` 同级）
>
> 范围：基于对**采集层（批量录入）/ 存储层 / LLM 客户端 / 追踪层读路径**实际代码的审查，梳理运行时可用性、并发安全、读侧性能与数据安全方向的目标。本文是对已落地代码的运行时复盘，**不改 DESIGN 的能力边界与四条不变量**，而是补强运行时健壮性。
>
> 与已有文档的关系：[improvement-plan.md](improvement-plan.md) 聚焦**分析正确性**（失分归属、知识库地基、归因闭环），本文聚焦**运行时**（并发、存储、外部依赖、性能），两者互补、不重叠；[batch-photo-input-design.md](batch-photo-input-design.md) 定义了批量录入的设计，本文是其**运行时健壮性的下游复盘**。
>
> 决策默认（评审可调）：
> - **存储**：SQLite 先开 WAL 稳态过渡，中期迁 PostgreSQL（不变量② derive-on-read 不受影响，换 URL 即可）；
> - **并发**：补 worker 异常兜底 + 看门狗，**MVP 不引入 Celery/RQ**（进程内线程池 + 启动回收继续沿用，补缺口而非换架构）；
> - **LLM**：补熔断 + 降级路径，保持 provider 无关接口层；
> - **读性能**：批量取事件 + 报告级缓存（标注失效条件，不违反不变量②，不存可变掌握度快照）。

---

## 0. 问题总览（优先级矩阵）

| # | 目标 | 主题 | 严重度 | 优先级 | 代码定位 | 阻塞上线？ |
|---|---|---|---|---|---|---|
| G1 | worker 异常未兜底，item 永久卡死 `parsing` | 并发入库 | 高 | **P0** | `ingestion/batch.py:121-155` | 是（数据卡死、静默失败） |
| G2 | SQLite 未开 WAL，读写互锁 | 存储 | 高 | **P0** | `db.py:21-30` | 是（并发下 `database is locked`） |
| G3 | `payload_json` PII 清洗不一致 | 数据安全 | 中 | **P1** | `ingestion/batch.py:218-259` | 是（PII 合规） |
| G4 | 掌握度查询 N+1 爆炸，报告级延迟 | 读性能 | 高 | **P1** | `pipeline/weakness.py:90-157,160` | 否（但真实负载下不可用） |
| G5 | LLM 单点硬依赖，无降级/熔断 | 外部依赖 | 高 | **P1** | `llm/client.py:155`、`ingestion/batch.py:322` | 是（provider 宕即全停） |
| G6 | stuck `parsing` 无看门狗 + tempfile 泄漏 | 并发入库 | 中 | **P1** | `ingestion/batch.py:83,47` | 否（需重启才恢复） |
| G7 | 可观测性缺失（无日志/指标） | 可观测性 | 中 | **P1** | 全局 | 否（但数据质量不可见） |
| G8 | 重试无 jitter / 无全局 deadline | 并发入库 | 中 | **P2** | `ingestion/batch.py:50` | 否 |
| G9 | 班级题均得分率 N+1 | 读性能 | 低 | **P2** | `pipeline/evidence.py:113-126` | 否 |
| G10 | SQLite 单文件 SPOF + 无 Alembic/备份 | 存储 | 中 | **P2** | `db.py:35`、`main.py:33` | 是（上线前） |
| G11 | 无认证 / 多租户字段空挂 | 数据安全 | 中 | **P2** | `models.py`、`main.py` | 是（上线前） |
| G12 | 并发数硬编码 + 无进度估计 | 并发入库 | 低 | **P2** | `ingestion/batch.py:66` | 否 |
| G13 | retry 重复付费（无幂等键） | 并发入库 | 低 | **P3** | `ingestion/batch.py:677` | 否 |
| G14 | `@app.on_event` 已废弃 | 工程债 | 极低 | **P3** | `main.py:29,42` | 否 |

> 已做对、**不改动**的部分见 §8：两段短事务不在 LLM 调用期间持写锁、`uq_tpl_student` + IntegrityError 去重、savepoint 隔离作答写入、`reconcile_stale` 启动回收。

---

## 1. 主题A：并发读卷入库的健壮性

批量录入的核心设计是扎实的（见 §8），但 worker 生命周期与异常处理有真实缺口。

### G1 · worker 异常未兜底，item 永久卡死 `parsing`（P0）

**现状（代码实证）** — `ingestion/batch.py:121-155`：

```python
def _process_async(item_id: int) -> None:
    # 事务1：置 parsing ...
    with _new_session() as s:
        item.status = PARSING
        s.commit()
    image_bytes = _read_tempfile(file_path)
    desc = _questions_desc(exam_template_id)
    payload, warnings = _call_llm_with_retry(desc, image_bytes)   # 可能抛
    with _new_session() as s:
        final_status = _persist_batch_result(...)                 # 内部只 catch IntegrityError
        _finalize_job(s, job_id)
        s.commit()
```

`_persist_batch_result`（`batch.py:198-278`）内部只捕获 `IntegrityError`（用于 duplicate 判定）。任何其他异常——`_persist_response_from_payload` 抛错、非 Integrity 的 DB 错误、`KeyError`——会直接逃出 worker。而 `submit_item`（`batch.py:62-72`）是 fire-and-forget，future 从不 `.result()`/`.exception()`，异常被静默吞掉。

**后果**：item 永远停在 `parsing`，job 永不 `finalize`；`reconcile_stale` 只在进程启动跑（`main.py:39`），不重启就恢复不了。一个偶发异常 = 一个永久孤儿 + 静默失败。

**目标**：任何 worker 异常都落到 item 终态，绝不留 `parsing` 孤儿，且失败可见。

**方案**：
- `_process_async` 整体包 `try/except Exception`，except 内开短事务把 item 标 `failed` + 记 warnings + `_finalize_job`；
- 关键路径加结构化日志（见 G7）；
- 测试：注入一个让 `_persist_response_from_payload` 抛 `RuntimeError` 的场景，断言 item 终态为 `failed`、job 为 `done`。

**验收**：人为注入异常后，无 `parsing` 孤儿；worker 线程不因单 item 失败而退出。

### G6 · stuck `parsing` 无看门狗 + tempfile 泄漏（P1）

**现状**：
- `reconcile_stale`（`batch.py:83-113`）仅在启动回收 `parsing`/`queued`，运行期无巡检。G1 的吞异常、或 worker 线程被 OOM kill，在下次重启前不可恢复。
- tempfile 用 `delete=False` 落盘（`routes.py` `_validate_and_persist`），仅在终态 `_TEMPFILE_DELETE_STATES`（`batch.py:47`，不含 `failed`）删除。走到终态前进程崩、或 G1 的吞异常，tempfile 留盘；`reconcile_stale` 不做 tempfile GC。

**目标**：运行期可自愈 stuck item；tempfile 不无限堆积。

**方案**：
- 加一个轻量看门狗（定时或惰性触发）：扫描 `parsing` 超过 N 分钟（如 2× `TIMEOUT`）的 item，改判 `failed` + "解析超时，可重试"；
- 启动 / 定期清扫孤儿 tempfile（按 mtime 兜底，item 已终态或不存在即删）。

**验收**：模拟 `parsing` 超 N 分钟后被自动改判 `failed`；磁盘 tempfile 总量有上界。

### G8 · 重试无 jitter / 无全局 deadline（P2）

**现状** — `ingestion/batch.py:50,322-339`：

```python
_RETRY_BACKOFFS = (2.0, 6.0)   # 固定，无 jitter
```

provider 返 429 时，3 个 worker 在相同偏移同时重试，加重限流（thundering herd）；`TIMEOUT=120`（`client.py:24`）下，单 item 最坏 3 次 ×120s ≈ 6 分钟才失败，无每个 batch job 的总时限。

**目标**：重试退避带抖动；批量 job 有总 deadline，慢调用不无限占用 worker。

**方案**：backoff 加随机抖动（±50%）；可选地为 job 级设总超时，超时则未完成 item 标 `failed`。

### G12 · 并发数硬编码 + 无进度估计（P2）

**现状** — `ingestion/batch.py:66`：`ThreadPoolExecutor(max_workers=3)` 硬编码。一个班 40-50 张卷，3 并发 × ~120s ≈ 30+ 分钟，前端只能轮询 `batch-jobs`，无 ETA、无队列深度。

**目标**：并发数可配置；job 暴露进度（已完成/总数）。

**方案**：`max_workers` 提为 `SC_BATCH_WORKERS` 配置；`ParseJob` 或 `batch-jobs` 响应带 `done/total` 计数。

### G13 · retry 重复付费（P3）

**现状** — `ingestion/batch.py:677` `retry_batch_item` 对 `failed` item 重读 tempfile + 重调 LLM。若首次 provider 侧已成功、仅落库失败，会重复付费。

**目标**：LLM 调用尽量幂等，避免重复计费。

**方案**：provider 支持时透传幂等键（OpenAI 兼容 `Idempotency-Key` 头），以 `item_id + attempt` 派生。

---

## 2. 主题B：存储层可用性

### G2 · SQLite 未开 WAL，读写互锁（P0）

**现状（代码实证）** — `db.py:21-30`：

```python
_connect_args = {"check_same_thread": False}
if settings.database_url.startswith("sqlite"):
    _connect_args["timeout"] = 15   # busy_timeout：写锁等待最多 15s
engine = create_engine(settings.database_url, connect_args=_connect_args, echo=False)
```

仅设了 `check_same_thread=False` + `timeout=15`，**未开 WAL**。SQLite 默认 `journal_mode=delete`，写者拿排他锁会**阻塞所有读者**。而本工作负载恰好是「读极重 + 并发写」：
- 不变量② derive-on-read：每次质量报告 / 掌握度查询都是成千上万次读（见 G4）；
- 同时 3 个 batch worker 在写（`batch.py`）。

两者互锁 → `database is locked` 风暴、15s 超时、请求卡死。

**目标**：读与写不互锁，并发下不出现锁等待超时。

**方案**：连接初始化开 WAL（迁 PG 前的稳态方案）：

```python
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.close()
```

**验收**：并发 batch 写入期间，质量报告/掌握度查询不阻塞、不报 `database is locked`；WAL 模式可经 `PRAGMA journal_mode` 确认。

### G10 · SQLite 单文件 SPOF + 无 Alembic/备份（P2）

**现状**：
- `db.py:35-39` 用 `create_all` 建表，无 Alembic 迁移。`main.py:33-35` 那个 `migrate_kb_archived` 启动 hack 已暴露痛点：schema 演进靠打补丁，跨版本有丢数据风险；
- `sc.db` 单文件，无备份策略，损坏即全量丢失。

**目标**：schema 变更可追踪、可回滚；数据可恢复。

**方案**：引入 Alembic 托管迁移（替换 `create_all` + 手工 migrate 脚本）；定义备份策略（定时复制 / `.backup` 命令），迁 PG 后由 PG 备份覆盖。

**验收**：一次 schema 变更有对应 migration，可 upgrade/downgrade；存在可验证的备份恢复演练。

---

## 3. 主题C：外部依赖可用性

### G5 · LLM 单点硬依赖，无降级/熔断（P1）

**现状（代码实证）**：
- `client.py:155-177` `get_client`：未配置时 `raise LLMError`（mock 不兜底）→ 拍照录入整体不可用；
- 单 provider 端点 = SPOF；`_call_llm_with_retry`（`batch.py:322-339`）仅 2 次固定重试，无熔断、无 fallback 模型、无降级（如「标为待解析、入队稍后重试」）。

**目标**：provider 短时不可用时系统不整体卡死，可降级运行或快速失败。

**方案**（分档）：
- 近期：`_call_llm_with_retry` 连续失败超阈值时熔断一段时间，期间直接 fast-fail，避免 3 个 worker 都耗在 120s 超时上拖垮整条流水线；
- 中期：失败 item 进「待重试」队列而非直接 `failed`；provider 无关层支持 fallback 模型（vision/text 各配主备）；
- 降级路径：拍照解析不可用时，引导教师走 Excel / 手工录入兜底（已有入口）。

**验收**：模拟 provider 持续 5xx，批量 job 在熔断窗口内 fast-fail 而非每 item 等满 6 分钟；存在非 LLM 的兜底录入路径。

---

## 4. 主题D：读侧性能

### G4 · 掌握度查询 N+1 爆炸，报告级延迟（P1）

**现状（代码实证）** — `pipeline/weakness.py:90-157`：

```python
def assess_student_kps(...):
    for kp_id in graph.grade7_kp_ids():          # 每个 kp
        events = get_events(session, student_id, kp_id, as_of)   # 1 次查询
        ...
        a.mastery = mastery_at(session, student_id, kp_id, as_of)  # 又 1 次
    # 双基准判定：每个有效 kp 再算全班分布
    for a in valid:
        distribution = _class_mastery(session, graph, class_id, a.kp_id, as_of)  # 见下
```

`_class_mastery`（`weakness.py:160-174`）对每个 kp 全班循环，每个学生又调 `get_events` + `mastery_at`：

```python
def _class_mastery(...):
    for stu in select(Student).where(class_id == class_id):
        events = get_events(session, stu.id, kp_id, as_of)
        m = mastery_at(session, stu.id, kp_id, as_of)
```

量级：50 学生 × 50 kp ≈ 2500+ 次 `get_events`，`_class_mastery` 再乘一轮全班。一次质量报告/班级概览可能数秒到数十秒。**与 G2（无 WAL）叠加会很难看**。

**目标**：真实班级规模（~50 人 × ~50 kp）下，质量报告/掌握度查询在秒级返回。

**方案**（不改不变量②，不存可变快照）：
- **批量取事件**：把 `get_events` 的逐 (student,kp) 查询改为按 (class, kp 集合) 批量取，内存里分组计算；`mastery_of_events` 已是纯函数，可直接喂批量数据；
- **报告级缓存**：对一次报告计算的全班掌握度分布做请求级 / 短 TTL 缓存，失效条件 = 该班有新「已提交」作答（事件只追加，提交时间戳可判失效）；
- `_class_mastery` 与 `assess_student_kps` 复用同一批预取数据，避免重复扫描。

**验收**：50×50 规模合成数据下，质量报告端点 p95 < 2s（基线实测后定）；金标全量不退化。

### G9 · 班级题均得分率 N+1（P2）

**现状** — `pipeline/evidence.py:113-126` `_class_question_rates`：嵌套循环 responses × answers × 每次 `session.get(TemplateQuestion)`，每次 commit 都跑。bounded 但仍 O(学生×题) 查询。

**目标**：commit 派生证据时查询数不随学生数线性膨胀。

**方案**：一次 `select` 取出模板全部 `TemplateQuestion` 进 dict，循环内查内存而非逐条 `session.get`。

---

## 5. 主题E：数据安全

### G3 · `payload_json` PII 清洗不一致（P1）

**现状（代码实证）** — `ingestion/batch.py:218-259`：

```python
detected_name = str(payload.get("student_name") or "").strip() or None
item.detected_name = detected_name
item.payload_json = payload          # ← 整份 LLM 响应（含 student_name）落库
...
# MATCHED 分支：
item.matched_student_id = stu_id
item.detected_name = None            # ← # matched 清空
item.response_id = response.id
# payload_json 未清理
# DUPLICATE 分支同理：detected_name = None，payload_json 未清理
```

`detected_name` 在 matched/duplicate 时被显式置 None（PII 最小化，注释「matched 清空」「duplicate 清空」明示意图），但 `payload_json` 里同样含 `student_name`，**matched/duplicate 后未清**。UNMATCHED 保留是文档明说的「待指派供教师参考」——但 matched 后一边清 detected_name、一边留 payload_json，意图不自洽：一个看起来在做 PII 擦除、实则没擦干净的面。

**目标**：终态匹配后，卷面姓名不在任何字段残留；PII 擦除意图自洽。

**方案**：matched/duplicate 终态时，从 `payload_json` 抹除 `student_name`（或整字段置 None，作答数据已落 `ExamResponse`/`ResponseAnswer`，item 不必再留全量 payload）；并核查 `batch-jobs`/`get_batch_job` 序列化是否回传 `payload_json`，若是则一并收敛。

**验收**：matched/duplicate item 的 `payload_json` 不含原始姓名；API 响应不回传卷面姓名（unmatched 指派期除外）。

### G11 · 无认证 / 多租户字段空挂（P2）

**现状**：`models.py` 全表带 `school_id`，但「MVP 不建权限体系」；`main.py` 无任何鉴权中间件。任何调用方可读任何学校数据。README 对此是诚实的，但属上线阻塞项。

**目标**：上线前有教师级鉴权与数据隔离。

**方案**：引入轻量鉴权（学校/班级维度）；路由层按 `school_id` 过滤。非本文重点，列为上线前必做项。

---

## 6. 主题F：可观测性与工程债

### G7 · 可观测性缺失（P1）

**现状**：无结构化日志、无指标。批量队列深度、LLM 延迟/失败率、parse/tag 置信度分布、SQLite 锁等待——全部不可见。G1 的静默失败正是其症状之一。对一个**价值取决于数据质量**的系统，置信度分布与解析失败率不可观测 = 数据质量不可见。

**目标**：关键运行时行为可观测，失败可定位。

**方案**：
- 结构化日志：worker 生命周期、LLM 调用（成功/失败/重试/耗时）、commit 派生事件数；
- 指标（可先打日志、后接 Prometheus）：batch 队列深度、worker 利用率、LLM p95 延迟与错误率、parse/tag 置信度直方图、`database is locked` 计数。

**验收**：一次批量上传后，可从日志/指标还原每 item 的解析耗时、匹配结果、失败原因。

### G14 · `@app.on_event` 已废弃（P3）

**现状** — `main.py:29,42`：用 `@app.on_event("startup"/"shutdown")`，新版 Starlette 已废弃，要 lifespan。现能用，未来升级会断。

**方案**：迁 `lifespan` 上下文管理器，`init_db`/`migrate`/`reconcile_stale`/`batch.shutdown` 统一收口。

---

## 7. 落地路线图

### 阶段一（P0 · 止血 — 让并发入库不死锁、不卡死）

- **G1**：`_process_async` 异常兜底 + item 落 `failed`（小改、风险低、收益高）；
- **G2**：SQLite 开 WAL + `busy_timeout`（一行 PRAGMA，可用性/吞吐立竿见影）；
- 回归：全量 101 测试不退化 + 并发写入下读路径不报锁错。

### 阶段二（P1 · 补强 — 真实负载可用、数据安全自洽）

- **G3**：`payload_json` PII 清洗；
- **G4**：掌握度查询批量取事件 + 报告级缓存（真实班级规模响应时间决定项）；
- **G5**：LLM 熔断 + 失败入待重试队列 + 兜底降级路径；
- **G6**：stuck parsing 看门狗 + tempfile GC；
- **G7**：结构化日志 + 关键指标。

### 阶段三（P2/P3 · 演进与上线前）

- **G10**：Alembic 迁移 + 备份策略；
- **G11**：鉴权与多租户隔离（上线前必做）；
- **G8 / G9 / G12**：重试 jitter、题均 N+1、并发数配置化、进度暴露；
- **G13 / G14**：LLM 幂等键、lifespan 迁移。

---

## 8. 不改动项（显式声明，避免反复讨论）

批量录入以下设计是**对的**，本次改进不动其结构，只在其上补缺口：

1. **两段短事务，LLM 调用期间不持写事务**（`batch.py:121-155`）——worker 在 LLM 调用前提交事务1、调用后开事务2，避免长事务持锁。保留。
2. **并发去重靠 `uq_tpl_student` 唯一约束 + `IntegrityError`**（`batch.py:262`），非先查后建——两 worker 并发解析同一学生卷，一个 matched、一个经 savepoint 回滚判 duplicate。保留。
3. **`session.begin_nested()` SAVEPOINT 隔离作答写入**（`batch.py:251`）——只回滚作答写入，不动 item 状态。保留。
4. **`reconcile_stale` 启动回收**（`batch.py:83`）——崩溃遗留的 parsing/queued item 改判 failed。保留（G6 只在其上补运行期看门狗，不替换）。
5. **tempfile 脱离请求生命周期**（`routes.py` `_validate_and_persist`，`delete=False` + 存路径）——handler 落临时文件、worker 按 `file_path` 读。保留（G6 只补 GC）。
6. **不引入 Celery/RQ 持久化工作队列**——MVP 取舍确认，进程内线程池 + 启动回收 + 看门狗（G6）即满足可用性目标，不为 MVP 引入额外基础设施。
7. **不变量② derive-on-read 不动摇**——G4 的缓存标注失效条件、不存可变掌握度快照；G2 的 WAL / G10 的迁 PG 均不改推导语义。

---

## 附：与 `improvement-plan.md` 的边界

| 维度 | `improvement-plan.md` | 本文档 |
|---|---|---|
| 关注 | 分析正确性（失分归属、知识库地基、归因闭环、标注质量） | 运行时（并发、存储、外部依赖、性能、数据安全） |
| 触及层 | 追踪/归因/知识库/采集的**算法与建模** | 采集/存储/LLM/读路径的**运行时行为** |
| 交集 | 无（互补） | 无（互补） |
| 共同约束 | 均不改四条不变量与能力边界 | 同 |

两份文档的 P0 互不冲突：`improvement-plan` 的 P0 是「让精度可观测、地基可信任」，本文的 P0 是「让并发入库不死锁、不卡死」。可并行推进。
