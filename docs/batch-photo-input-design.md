# 批量拍照录入学生卷 - 设计文档 v0.3

> 状态：待评审 · 所在目录：backend/（与后端代码同仓，方便实现时对照）
>
> 版本演进：
> - v0.2：对照实际代码补全数据模型约束、ParseJob 复用方式、prompt 变体、后台短事务与生命周期、前端 API 签名、测试陷阱与状态映射表（以 **〔补〕** 标注）。
> - v0.3：二轮缺陷评审，修复 prompt 版本号溯源、包含匹配多重命中、上传字节生命周期、文件校验、失败项重试/丢弃、重启僵尸回收、LLM 重试、sync 守卫、detected_name 留存策略等（以 **〔v0.3〕** 标注）。

---

## 1. 背景与目标

现有学生卷录入是"逐人拍照"：教师在采集页每行的「拍照」按钮前，必须先从名单里选中学生，且一次只能传一张，上传后 LLM 同步解析（阻塞请求）。对整班 30~50 人的场景，操作繁琐、等待感强。

本次要解决的问题：

| 现状痛点 | 本次目标 |
|---|---|
| 一次只能传一张，逐行点按 | 一次上传多张学生答卷照片 |
| 教师须先选中学生，照片与人对不齐 | 解析时自动读取卷面姓名，与班级名单匹配 |
| 上传后同步阻塞，前端干等 | 后台解析，前端即时拿到任务并轮询状态 |
| 录入页固定当前班级 | 录入入口可切换/筛选班级 |

## 2. 需求拆解（对应验收点）

1. **批量上传**：一次选择 N 张图片，立即返回任务，不阻塞。
2. **班级筛选**：录入页顶部班级选择器，切换后列表/上传目标随之变化。
3. **姓名解析与匹配**：模型从卷面姓名栏读出姓名 -> 规范化后与班级名单匹配（精确->包含->未匹配）。
4. **后台执行与状态展示**：任务状态机可见；每个文件有独立状态；未匹配项可由教师指派到具体学生；**〔v0.3〕** 失败项可重试、不可读项可丢弃。

## 3. 总体流程（时序）

```
教师                          FastAPI                      后台线程池                LLM(qwen3.7-flash)
 | ① 上传 N 张照片 -----------> |                            |                          |
 |  (photo-batch, multipart)   | 校验图片/大小 -> 落 tempfile |                         |
 |                             | 建 ParseJob + N 个 item     |                          |
 |                             | item.file_path = tempfile   |                          |
 | <-- {job_id, items} -------- | --> _executor.submit(item_id, file_path) ---> |        |
 | ② 轮询 job 状态 (2.5s) ----> |                            | 逐 item:                  |
 | <-- {item_i: 解析中/已匹配} -- | <------------------------- | 短事务1: item=parsing 提交 |
 |                             |                            | 读 tempfile 字节           |
 |                             |                            | --未遮罩原图+姓名要求 prompt-->| 读姓名+读得分
 |                             |                            |   (LLMError 重试 1-2 次)    |
 |                             |                            | <-- {student_name,answers} |
 |                             |                            | 匹配名单 -> 短事务2: 写 ExamResponse |
 |                             |                            | 删 tempfile(终态非 failed)   |
 |                             |                            | 收尾: job.done             |
 | ③ 未匹配项: 指派/丢弃 -------> | POST assign/discard        |                            |
 |    失败项: 重试 ------------> | POST retry(从 file_path)   |                            |
 | <-- {response_id/status} --- |                            |                            |
 | ④ 审核台核对低置信 -> 提交    |                            |                            |
```

**〔补〕** worker 在调 LLM 前后分别用独立短事务更新状态与落库，**不在 LLM 调用期间持有写事务**（LLM 单次可达数十秒，持锁会拖垮并发）。

**〔v0.3〕** 上传字节必须脱离请求生命周期：handler 内 `await f.read()` -> `tempfile.NamedTemporaryFile(delete=False)` -> 把**路径**存入 `item.file_path` 再 submit。`UploadFile` 背后是 `SpooledTemporaryFile`，请求结束会被 FastAPI 关闭，worker 若直接 `file.read()` 会拿到空/已关闭数据。详见 §5/§7。

## 4. 数据模型（新增 1 张表）

**`parse_batch_item`**（models.py 追加；`init_db()` 的 create_all 自动建表，存量库无需迁移）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| parse_job_id | FK -> parse_job.id | 所属批任务 |
| exam_template_id | FK -> exam_template.id **〔补〕** | 冗余存考试 id，便于按考试列批任务、指派校验班级归属 |
| file_name | String(200) | 上传文件名 |
| file_path | String(300) NULL **〔v0.3〕** | tempfile 路径；failed 项保留供重试，其余终态由 worker 删除 |
| detected_name | String(100) NULL | 模型读出的卷面姓名；**〔v0.3〕** 进入 matched/duplicate/unmatched(指派后)/discarded 即清空（见 §9） |
| matched_student_id | FK -> student.id NULL | 匹配到的学生 |
| match_confidence | Float | **姓名**匹配置信度（精确 1.0 / 包含 0.8）；与答题判读置信度 `ResponseAnswer.parse_confidence` 是两回事，见 §6 |
| status | String(20) | queued \| parsing \| matched \| unmatched \| failed \| duplicate \| **discarded〔v0.3〕** |
| response_id | FK -> exam_response.id NULL | 落库的作答；duplicate 时指向**既有**那条 |
| warnings | JSON | 该文件的解析警告（**〔v0.3〕** 不得内嵌原始姓名，防 PII 进日志） |
| payload_json | JSON NULL | 模型返回的 {student_name, answers}；未匹配指派时**无需重调 LLM** |
| created_at | DateTime **〔v0.3〕** | 建项时间；用于 job 列表排序与僵尸排查 |

**〔v0.3〕** 建议加索引 `parse_job_id`、`exam_template_id`（按 job/考试查 items 的主路径）。

**〔补〕关键既有约束（无需新增，直接复用）**：`ExamResponse` 已有 `UniqueConstraint("exam_template_id", "student_id", name="uq_tpl_student")`（models.py:208）。这是 duplicate 检测的**最终保障**--并发 worker 同时匹配到同一学生时，应用层先查可能双双通过（TOCTOU），但 DB 唯一约束必有一方触发 `IntegrityError`。batch worker 必须 `try/except IntegrityError -> status=duplicate`，**不要**只靠先查后建。

**〔补〕ParseJob 复用方式（不加列）**：`ParseJob` 现有字段 `id/target/model_version/prompt_version/status/cost`，无 `kind`/`source`。注意 `create_all` **只建缺失的表，不会给已有表加列**--若给 `ParseJob` 加 `kind` 列，存量库不会自动生效，需手写迁移。故采用 **target 字符串约定**区分：

- 单张模板：`target=f"template:{name}"`（既有）
- 单张学生卷：`target=f"response:{template_id}:{student_id}"`（既有）
- 批量：`target=f"batch:{exam_id}"`（新增，一个批次一个 ParseJob）

`GET /exams/{id}/batch-jobs` 按 `ParseJob.target == f"batch:{exam_id}"` 过滤，无需 schema 变更。

**〔补〕item 状态 ↔ ExamResponse 状态映射**（两套状态机独立，勿混淆）：

| item.status | 含义 | 是否创建 ExamResponse | response_id | tempfile |
|---|---|---|---|---|
| queued | 未开始 | 否 | NULL | 保留 |
| parsing | LLM 进行中 | 否 | NULL | 保留 |
| matched | 姓名命中名单 | 是，`status="待审核"` | 新建的 id | 删除 |
| duplicate | 命中但该生本场已有作答 | 否（`uq_tpl_student`） | 指向既有 | 删除 |
| unmatched | 读到姓名但名单无匹配/歧义 | 否，等指派 | NULL | 删除（payload_json 已落） |
| failed | LLM 失败 / 无有效 answers | 否 | NULL | **保留供重试** |
| discarded **〔v0.3〕** | 教师主动放弃 | 否 | NULL | 删除 |

`ExamResponse.status` 走既有状态机（上传->解析中->**待审核**->已提交）；batch 落库一律置 `待审核`。提交复用既有 `commit_exam`（见 §7）。

## 5. 后端接口（新增 6 个）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/exams/{id}/photo-batch` | multipart `files[]`(list[UploadFile]) + Form `sync=false` | `{job_id, items:[{id,file_name,status}]}` |
| GET | `/exams/{id}/batch-jobs` | - | `{jobs:[{job_id,status,counts:{...}}]}` |
| GET | `/batch-jobs/{job_id}` | - | `{job_id,status,items:[{id,file_name,detected_name,matched_student_id,matched_student_name,status,match_confidence,warnings}]}` |
| POST | `/batch-items/{item_id}/assign` | JSON `{student_id}` | `{response_id,status}` |
| POST | `/batch-items/{item_id}/retry` **〔v0.3〕** | - | `{id,status}` |
| POST | `/batch-items/{item_id}/discard` **〔v0.3〕** | - | `{id,status:"discarded"}` |

- `sync=true` 仅在测试用（内联执行，免等待线程池；且复用请求会话，见 §7/§10）。
- 指派用 `payload_json` 落库，重复作答返回 `duplicate`，不重复建卷。

**〔补〕`POST /exams/{id}/photo-batch` 实现要点**：FastAPI 签名 `files: list[UploadFile] = File(...), sync: bool = Form(False)`；一个批次建**一个** `ParseJob(target=f"batch:{exam_id}", status="running")`。

**〔v0.3〕文件校验（handler 内，submit 前）**：
- **类型**：每个文件 `PIL.Image.open(io.BytesIO(await f.read())).verify()`，非图片或损坏返回 `400`（整批拒绝，列出非法文件名）。verify 后需重新 `Image.open` 取字节（verify 会重置流）。
- **大小**：单文件上限 10MB、整批上限 100MB、整批文件数上限 50；超限返回 `413`/`400`。批量把既有单张端点的 OOM/卡死风险放大 N 倍，必须设防。
- **持久化**：校验通过后写入 `tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")`，路径存 `item.file_path`，再 submit。字节不再进 `UploadFile`，脱离请求生命周期。

**〔v0.3〕`sync` 守卫**：`sync=true` 仅在 `SC_LLM_PROVIDER=mock` 或 `settings.allow_sync_batch=True` 时生效，否则强制按 `sync=false`。避免生产环境一次 `sync=true` 同步阻塞请求线程数十分钟（50 张 ×~15s）。

**〔v0.3〕`POST /batch-items/{item_id}/retry`**：仅 `status==failed` 可调；校验 `file_path` 文件仍存在（不存在则 400 提示重新上传）；重置 `status=queued`、清 warnings、`executor.submit` 重跑。成功后按正常路径落 matched/duplicate/unmatched/failed。

**〔v0.3〕`POST /batch-items/{item_id}/discard`**：仅 `status in (unmatched, failed)` 可调；置 `status=discarded`、`detected_name=NULL`、删 `file_path` tempfile。退出未处理队列。

**〔补〕`POST /batch-items/{item_id}/assign` 契约**：
- 前置：`item.status == "unmatched"` 且 `payload_json.answers` 非空；`failed`/`matched`/`duplicate`/`discarded` 调用返回 400。
- 班级归属：`student.class_id == template.class_id`，否则 400。
- 落库：调用共享 helper `_persist_response_from_payload`（见 §12），与单张路径一致。
- 去重：`try: flush() except IntegrityError: -> status=duplicate, response_id=既有.id`。
- 成功：`item.status=matched`、`matched_student_id=student_id`、`response_id=新.id`、`detected_name=NULL`〔v0.3〕、删 tempfile〔v0.3〕。

## 6. 姓名解析与匹配

**〔补〕Prompt 升级（新增常量，不改动既有）**：既有 `RESPONSE_SYSTEM` 铁律②为"忽略图片中任何姓名/班级信息"--与批量"需读姓名"冲突。故**新增** `RESPONSE_BATCH_SYSTEM` + `response_batch_user_prompt()`，**不得改动** `RESPONSE_SYSTEM`（单张路径仍用旧 prompt + 遮罩）。新 prompt 输出 `{student_name, answers:[...]}`，铁律改写为"读取卷面姓名栏**仅用于名单匹配**，不得输出其他个人信息；姓名绝不影响判分"。

**〔v0.3〕版本号独立，不升全局常量**：`PROMPT_VERSION = "parse-v0.1.0"` 是 prompts.py 的**单一全局常量**，被模板解析（photo.py:75）与学生卷解析（photo.py:216）共用并记入各自 `ParseJob.prompt_version`。若直接改成 v0.2.0，**没改过 prompt 的单张/模板路径**也会被记成 v0.2.0，破坏溯源（DESIGN 溯源原则以 `parse_job.prompt_version` 为审计依据）。故新增 `RESPONSE_BATCH_PROMPT_VERSION = "parse-v0.2.0"`，batch 的 ParseJob 记它；`PROMPT_VERSION` 保持 `parse-v0.1.0`。本质应走"每条 prompt 各自带版本号"。

- 批量路径不遮罩原图；单张路径保持现状遮罩。
- 批量 worker：`client.parse_json(RESPONSE_BATCH_SYSTEM, response_batch_user_prompt(desc), image_bytes)`，`image_bytes` 为**未遮罩**原图。

**匹配算法**（名单来源：`Student WHERE class_id = exam.class_id`）：

1. 规范化：`re.sub(r"[\s·.・]", "", name).lower()`（去空白/中间点/大小写）。
2. ① 精确命中：`norm(detected) == norm(name_or_alias)` 或 `== norm(external_code)` -> `match_confidence=1.0`。
3. ② 包含匹配（仅当无精确命中）：取较短串长度 ≥2 且 `norm(short) in norm(long)`，**仅对 `name_or_alias`** -> `match_confidence=0.8`。
4. ③ 否则 `unmatched` 交人工指派。

**〔v0.3〕包含匹配的两条硬约束**：
- **`external_code` 不参与包含匹配**。学籍号是 ID，子串匹配毫无意义（"001" 命中 "1001" 必误）。external_code 只做精确等值（步骤②）。
- **多重命中判歧义**：包含匹配若同时命中 **>1 名学生**（如名单含"王小明""李小明"，读到"小明"），**不得任选一个**，直接 `unmatched`（warnings 记"姓名包含匹配存在多名候选"）。只有命中唯一学生才置 matched。

**〔补〕两种置信度切勿混淆**：

| 字段 | 含义 | 取值 | 作用 |
|---|---|---|---|
| `parse_batch_item.match_confidence` | **姓名**匹配置信度 | 1.0 / 0.8 | 只决定 matched/unmatched 路由，不写入 ExamResponse |
| `ResponseAnswer.parse_confidence` | **得分判读**置信度（既有） | 0~1 | 决定低置信审核队列：<0.6 强制人工、0.6~0.9 高亮、≥0.9 自动通过（`AUTO_PASS=0.9`） |

一个 item 可能 `match_confidence=0.8` 而 answers 全部 `parse_confidence=0.95`。**0.8 姓名匹配不会"自动提交"任何东西**--所有 batch 作答一律落 `待审核`，是否进报告由既有 `commit_exam` 决定。

## 7. 后台执行

- 新模块 `app/ingestion/batch.py`：模块级 `ThreadPoolExecutor(max_workers=3)`。
- worker 自开 `SessionLocal()` 会话（**不复用请求会话**，避免跨线程共享）。
- 每 item：`parsing` -> 调视觉模型 -> 写结果 -> `matched/duplicate/unmatched/failed`；异常兜底 `failed`。
- 收尾：每 item 完成后检查同 job 下是否还有 queued/parsing，无则 `ParseJob.status=done`。
- 并发写：db.py `connect_args` 加 `"timeout": 15`（SQLite 写锁等待）。

**〔补〕短事务，不在 LLM 期间持锁**：worker 拆两段独立事务：
1. 事务1（调 LLM 前）：`item.status=parsing`、`ParseJob.status=running` -> commit/close，再调 LLM。
2. 调 LLM（无 session）。
3. 事务2（LLM 返回后）：匹配名单 -> `_persist_response_from_payload` 落库（`try/except IntegrityError -> duplicate`）-> 写 item 结果 -> commit/close。

**〔v0.3〕字节读取与 tempfile 清理**：worker 从 `item.file_path` 读字节（不依赖 UploadFile）。终态落定后：`matched/duplicate/unmatched/discarded` 删除 tempfile；**`failed` 保留**供 retry。删除用 `try/except OSError` 兜底，不阻塞状态写入。

**〔v0.3〕LLM 瞬时失败重试**：`LLMError` 分两类--网络/超时/5xx（可重试）vs JSON schema 解析失败/空返回（不重试，重试也没用）。可重试类做 1-2 次指数退避（2s、6s）再判 `failed`。批量 50 张挂 3-4 张是大概率事件，重试把偶发失败压到接近 0。注意 `MockLLMClient` 抛的 `LLMError("无预设响应")` 属不可重试类。

**〔补〕duplicate 靠 IntegrityError 兜底**：事务2 中 `flush()` 触发 `IntegrityError` 时回滚 response 写入，置 `item.status=duplicate`、`response_id=既有.id`（重查一次）。

**〔补〕SessionLocal 动态获取（测试陷阱）**：`batch.py` 若模块顶部 `from app.db import SessionLocal` 会绑定原始 SessionLocal，而 test_photo.py 夹具只替换 `dbmod`/`routes_mod` 的。规避：worker 内 `from app import db as dbmod; with dbmod.SessionLocal() as s:` 动态获取。`sync=true` 路径直接用请求 `db`，完全不碰 SessionLocal（见 §10）。

**〔补〕线程池生命周期**：`main.py` 新增 `@app.on_event("shutdown")` 调 `batch.shutdown()` -> `executor.shutdown(wait=False, cancel_futures=True)`。

**〔v0.3〕启动僵尸回收**：进程内线程池的 future 全在内存，`uvicorn --reload`/崩溃后，在途 item 永远停在 `parsing`、job 停在 `running`，直到被 GET 才回收。故 `@app.on_event("startup")`（在 `init_db` 后）加全局兜底：把所有 `status=running` 的 batch ParseJob 下仍 `parsing`/`queued` 的 item 改判 `failed`（warnings 记"服务重启中断，可重试"），job 置 `done`。这些 failed 项的 `file_path` tempfile 仍在，教师可点 retry 恢复。MVP 接受不引入持久化工作队列（Celery/RQ）。

**〔补〕僵尸 parsing 收尾**：`GET /batch-jobs/{job_id}` 与收尾逻辑中，若 `ParseJob.status=="done"` 但仍有 item 处于 `parsing`，改判 `failed`。

**〔补〕提交复用既有 `commit_exam`**：batch 不新增提交端点。产出的 `待审核` ExamResponse 与单张/excel 路径混在一起，教师核对后调既有 `POST /exams/{id}/commit`。故"job.done"≠"已提交"--前端须区分：job 全终态后引导去**审核台**，提交走既有 Collect 页。

## 8. 前端交互

- **班级筛选**（Exams.tsx 顶部）：`listClasses()` 选择器，切换 `navigate(/c/{id}/exams)`。
- **批量录入卡**（Collect.tsx 顶部）：
  - `input type=file multiple accept=image/*` -> 选择即 `photoBatch(eid, files)` -> 显示任务。
  - 轮询：job 存在且仍有 queued/parsing 项时 `setInterval(2.5s)` 拉 `batchJob`；cleanup 清除定时器。
  - 每文件一行：文件名 / 识别姓名（"识别中…"占位）/ 状态徽章 / 操作。
  - 未匹配行：班级学生下拉 + 「指派」/「丢弃」-> 重拉。
  - 摘要，全部终态后引导「去审核台核对 -> 提交」。

**〔补〕班级筛选复用既有路由**：App.tsx 已是 `/c/:classId/exams` 全程带 classId，根路径即 `ClassPicker`。Exams 顶部选择器选中 `navigate(\`/c/${id}/exams\`)` 即可，无需新路由。

**〔补〕轮询终止条件**：`useBatchJob(jobId)` hook--`job.status==="done"`（所有 item 进终态）即 `clearInterval`；jobId 为空/卸载也停。

**〔补〕新增 api.ts 函数与 types**：

```ts
// types.ts
export type BatchItemStatus = "queued"|"parsing"|"matched"|"unmatched"|"failed"|"duplicate"|"discarded";
export interface BatchItem { id:number; file_name:string; detected_name:string|null;
  matched_student_id:number|null; matched_student_name:string|null;
  status:BatchItemStatus; match_confidence:number|null; warnings:string[]; }
export interface BatchJob { job_id:number; status:string; items:BatchItem[]; }

// api.ts（multipart 需新增 multipartMulti 支持 list[UploadFile]）
export const photoBatch = (examId:number, files:File[], sync=false) =>
  request<{job_id:number; items:{id:number;file_name:string;status:string}[]}>(
    `/exams/${examId}/photo-batch`,
    { method:"POST", body: multipartMulti({ sync:String(sync) }, files, "files") });
export const listBatchJobs = (examId:number) =>
  request<{jobs:{job_id:number;status:string;counts:Record<string,number>}[]}>(`/exams/${examId}/batch-jobs`);
export const batchJob = (jobId:number) => request<BatchJob>(`/batch-jobs/${jobId}`);
export const assignBatchItem = (itemId:number, student_id:number) =>
  request<{response_id:number;status:string}>(`/batch-items/${itemId}/assign`, json({student_id}));
export const retryBatchItem = (itemId:number) =>   // 〔v0.3〕
  request<{id:number;status:string}>(`/batch-items/${itemId}/retry`, { method:"POST" });
export const discardBatchItem = (itemId:number) =>   // 〔v0.3〕
  request<{id:number;status:"discarded"}>(`/batch-items/${itemId}/discard`, { method:"POST" });
```

`multipart` 现仅支持单文件，需加 `multipartMulti(fields, files:File[], fileKey)` 逐个 `fd.append(fileKey, f)`。

**〔补〕状态徽章映射**：queued=灰、parsing=琥珀呼吸、matched=绿、unmatched=红+指派下拉、duplicate=琥珀、failed=红+**重试**〔v0.3〕、discarded=灰删除线〔v0.3〕。复用既有 `Badge` tone 体系。

**〔补〕duplicate 行**：展示"该生已有作答（重复上传）"+「查看既有」跳转审核台；无指派/重传按钮。

**〔v0.3〕操作列按状态分发**：
- unmatched：「指派」下拉 + 「丢弃」
- failed：「重试」+ 「丢弃」
- matched/duplicate/discarded：无操作（discarded 显示删除线 + "已放弃"）

**〔v0.3〕摘要措辞**：不写含糊的"完成 X / 总 Y"（failed 也算完成会误导）。拆成"成功 {matched+duplicate} / 待指派 {unmatched} / 失败 {failed} / 已放弃 {discarded} / 总 {N}"，全终态后引导去审核台。

**〔v0.3〕batch 完成联动矩阵**：batch 卡与 Collect 矩阵（`examResponses`，`useAsync` 一次性加载）是两套视图。job 全终态时调 `matrix.reload()`，避免"8 matched"但矩阵仍"未采集"的不同步。指派/重试/丢弃后同样 reload。

**〔v0.3〕commit 后陈旧标记**：提交后 ExamResponse 锁成"已提交"，但 batch item 仍显示 matched。job 摘要带"本场已提交"标记，已提交的 batch 视图置只读（隐藏所有操作按钮），避免教师对已锁定数据误操作。

## 9. PII 与合规说明

- 批量路径为匹配名单需读取姓名 -> 取消该路径的姓名区遮罩；**姓名仅用于名单匹配，不进入任何报告、不用于判分、不落额外字段**。
- 单张路径保持遮罩不变。DESIGN.md §13 呈现伦理不受影响。

**〔补〕对 DESIGN.md §13 的偏离说明**：§13"PII 剥离前置"是既有硬约束。批量路径**定向放宽**：仅批量路径取消遮罩、prompt 铁律限定"姓名仅用于匹配、不得输出其他 PII、不影响判分"。需在 DESIGN.md §13 追加"批量录入例外"批注。

**〔v0.3〕`detected_name` 留存策略**：单张路径从不落名（遮罩 + 不输出）。批量把 `detected_name` 写入 `parse_batch_item`，是**新增的持久化 PII 表面**，必须有留存边界：item 一旦进入 `matched`/`duplicate`/`discarded`，或 `unmatched` 被指派后，立即置 `detected_name=NULL`（匹配/指派完成后姓名已无用途）。仅 `unmatched` 待指派期间保留供教师参考。`failed` 项的 detected_name 也清空（重试会重新读）。此外 **warnings 不得内嵌原始姓名**（如改写为"姓名包含匹配存在多名候选"而非"张三存在多名候选"），防 PII 进日志。

## 10. 测试与验证

**单元/接口测试**（test_photo.py，`sync=true` + MockLLM 预设含 `student_name`）：
- 姓名命中 -> item matched + response 落库
- 未命中 -> unmatched -> assign -> response 落库
- 同名二次上传 -> duplicate 不重复落库
- mock 无预设 -> item failed
- 既有 43 项保持全过

**〔补〕新增/细化用例**：
- **duplicate 走约束**：同 student 连续两次 batch，断言第二条 `duplicate`、`response_id` 指向第一条，ExamResponse 计数不增。
- **assign 边界**：对 `failed`/`discarded` 项调 assign 返回 400；非本班 student_id 返回 400。
- **包含匹配**：名单"王小明"、读出"小明" -> 0.8 matched、warnings 含"包含匹配"。
- **两置信度独立**：matched item 的 `ResponseAnswer.parse_confidence` 来自模型 answers。

**〔v0.3〕新增用例**：
- **包含匹配多重命中**：名单含"王小明""李小明"、读出"小明" -> `unmatched`、warnings 含"多名候选"（不任选）。
- **external_code 不参与包含**：名单 external_code="2024001"、读出"001" -> 不命中（unmatched），不因子串误匹配。
- **文件校验**：上传非图片/损坏文件 -> 400；单文件 >10MB -> 413；>50 文件 -> 400。
- **retry**：failed 项调 retry -> 重跑 -> matched；retry 前删其 tempfile -> 400 提示重新上传。
- **discard**：unmatched 项调 discard -> `discarded`、`detected_name=NULL`、tempfile 已删；对 matched 项调 discard -> 400。
- **启动僵尸回收**：构造 `parsing` 僵尸 item + `running` job，触发 startup 钩子后 -> item `failed`、job `done`。
- **LLM 重试**：mock 前两次抛网络类 LLMError、第三次返回正常 -> 最终 matched（验证退避重试；用 monkeypatch 跳过真实 sleep）。
- **sync 守卫**：`SC_LLM_PROVIDER!=mock` 时传 `sync=true` -> 仍走异步（job 立即返回）。
- **detected_name 清空**：matched/assigned/discard 后断言 `detected_name is None`。
- **prompt 版本隔离**：batch 的 ParseJob.prompt_version=="parse-v0.2.0"；同期单张路径仍=="parse-v0.1.0"。

**〔补〕MockLLMClient 顺序依赖**：`MockLLMClient` 按 `pop(0)` 返回，`sync=true` 串行顺序=提交顺序，故 `MockLLMClient([payload_s01, ...])` 顺序可预测--这是测试统一用 `sync=true` 的根本原因。

**〔补〕sync=true 用请求会话**：`sync=true` 在 handler 内用 `Depends(get_db)` 的 `db` 串行跑，不另开 SessionLocal，绕开线程池与 SessionLocal 绑定陷阱。

**真实冒烟**：mock 卷顶部含「姓名：S01…」与名单一致 -> 一次上传 8 张 `stu_*_w3.jpg` -> 轮询到 8 matched -> commit -> 出报告。
**浏览器冒烟**：Exams 页班级切换；Collect 页批量上传 -> 状态轮询 -> 失败项重试 -> 未匹配指派/丢弃 -> 审核台。

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 模型读姓名出错 -> 误匹配 | 匹配只做精确/包含，不猜测；多重命中判歧义进人工；`matched_student_id` 可复查；包含匹配 UI 标黄 |
| SQLite 并发写锁 | `timeout=15`；worker=3；每 item 独立短事务；不在 LLM 期间持锁 |
| 并发同生重复落库 | 靠既有 `uq_tpl_student` + `IntegrityError->duplicate`，不靠先查后建 |
| 线程池生命周期 | 测试 `sync=true` 不触线程池；shutdown 钩子 `executor.shutdown` |
| SessionLocal 绑定写错库 | worker 动态 `dbmod.SessionLocal()`；`sync=true` 用请求会话 |
| 轮询与路由切换 | useEffect cleanup 清定时器；job 全终态即停 |
| `create_all` 不给 ParseJob 加列 | 用 `target=f"batch:{exam_id}"` 约定，不加 `kind` 列 |
| MockLLM 顺序依赖 | 测试统一 `sync=true` |
| 进程崩溃留 parsing 僵尸 | startup 全局兜底改判 failed；failed 项可 retry 恢复 |
| **〔v0.3〕** 上传字节随请求关闭丢失 | handler 先落 `delete=False` tempfile，worker 读 `file_path` |
| **〔v0.3〕** 大批量原图 OOM | 单文件 10MB / 整批 100MB / 50 张上限；tempfile 落地不常驻内存 |
| **〔v0.3〕** 非图片/损坏文件卡 LLM | handler 内 `PIL.verify()` 校验，整批拒绝 |
| **〔v0.3〕** LLM 偶发失败放大 | 可重试类 LLMError 退避重试 1-2 次 |
| **〔v0.3〕** sync=true 阻塞生产请求 | sync 仅 mock/显式开关生效 |
| **〔v0.3〕** detected_name 长期留存 | 终态即清空；warnings 不内嵌姓名 |
| **〔v0.3〕** prompt 版本溯源失真 | batch 独立 `RESPONSE_BATCH_PROMPT_VERSION`，不动全局常量 |
| **〔v0.3〕** batch 视图与矩阵不同步 | job 全终态/指派/重试/丢弃后 `matrix.reload()` |

## 12. 实施顺序

1. 后端：models（加 `parse_batch_item`，含 `file_path`/`created_at`/`discarded`）-> prompts（新增 `RESPONSE_BATCH_SYSTEM` + **独立** `RESPONSE_BATCH_PROMPT_VERSION`，不动既有）-> **photo.py 提取 helper** `_persist_response_from_payload(session, template, student_id, payload, result) -> ExamResponse`（抽出现 photo.py:250-290，含 `_clamp_score`/`_conf`/选项处理）-> batch.py（worker + 短事务 + tempfile 清理 + LLM 重试 + `shutdown()` + `reconcile_stale()`）-> routes（6 端点 + 文件校验 + sync 守卫）-> db.py（`timeout=15`）+ main.py（startup 僵尸回收 + shutdown 钩子）。
2. 前端：types -> api（`multipartMulti` + 6 函数）-> Exams 班级筛选 -> Collect 批量卡 + `useBatchJob` 轮询 + retry/discard 按钮 + 摘要拆分 + 矩阵联动 + commit 只读标记。
3. 测试：v0.3 新增 9 用例（多重命中、external_code 不包含、文件校验、retry、discard、僵尸回收、LLM 重试、sync 守卫、detected_name 清空、版本隔离）+ 全量回归。
4. 冒烟：真实批量（含人为触发失败 -> retry）+ 浏览器走查。
5. 文档：DESIGN.md §13 追加"批量录入 PII 例外"批注。
