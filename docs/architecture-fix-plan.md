# 架构修复方案（architecture-fix-plan）

> 由 `/mattpocock-skills:improve-codebase-architecture` 衍生。
> 配套阅读：`architecture-review-*.html`（候选卡 + before/after 图）。
> 术语：架构用 `module / interface / depth / seam / adapter / leverage / locality / 删除测试`；域语言取自 README（无 `CONTEXT.md`，建议本方案落地时顺手建一个，见末尾）。

## 总览

| # | 候选 | 强度 | 核心动作 | 触及不变量 |
|---|---|---|---|---|
| 1 | 归因“打底舞” | Strong | 引入 `resolve_attributions` 深模块，诊断改为 derive-on-read | **恢复 ②** |
| 2 | routes.py 上帝模块 | Strong | 按域拆 router + 抽逻辑进 service/query 模块 | — |
| 3 | 报告生成器四合一 | Worth exploring | compute / render / persist / narrative 分层；narrative 纳入 LLM 闸门 | 强化 ③④ |
| 4 | commit_exam bool 开关 | Worth exploring | 去 flag，报告生成由调用方组合 | — |
| 5 | 跨栈重复 | Speculative | 统一 `_active_kb`；标签 codegen（已现 `易混淆` drift） | — |

**实施顺序**：先 5（去重，零风险铺垫）→ 1（首选，恢复不变量②）→ 3（报告分层，依赖 1 的归因接口）→ 2（拆 router，把 1/3 的模块安家）→ 4（commit seam，最后收尾，因脚本多处调用）。1 与 2 协同最优：抽诊断编排模块时正好装入新的归因解析模块。

**通用原则**：
- 深化 ≠ 拆文件。目标是让简单接口背后藏住实质实现。每一步都过**删除测试**：删掉它会浓缩复杂度（好）还是只是搬走（坏）？
- TDD：每个新深模块先写测试（RED）再实现（GREEN）。新模块的接口即测试面。
- 不破坏产品语义：「提交即自动生成」「教师否决跨重跑保留」「报告数字零幻觉」等行为不变，只改 seam 形状。
- 每步可独立合入、独立回滚；不搞大爆炸重构。

---

## 候选 1 ｜归因 derive-on-read（首选）

### 现状精析

`generate_student_diagnosis`（`reports/student_diagnosis.py:50-58`）直接读 `Attribution` 表里 `status=='active'` 的行；若某薄弱 kp 没有对应行，落到 `:164` 的「暂未匹配到规则成因」--**无报错、无异常**。

这些 active 行由 `run_attribution_for_student`（`pipeline/attribution.py:268-354`）写入。于是诊断生成有一个**隐含前置条件**：调用方必须先“打底”。当前 4 处调用方都靠人记着这步：

- `routes.py:842`（exam_id 路径）、`:853`（无 exam 无 as_of 路径）、`:861`（as_of 路径）
- `auto_generate.py:105-106`（提交后自动生成）

漏一处 → 静默错输出。这违背**不变量②**：mastery 是 derive-on-read（`mastery.py`），归因却是一份“手工打底的存储缓存”。

**根因**：`Attribution` 一张表混了三件事--
1. **系统推导的假设**（应像 mastery 一样 derive-on-read，纯函数 `attribute_assessment` 已存在）；
2. **人工裁决**（`overridden`：教师否决 `routes.py:1943` / 诊断题证伪 `attribution.py:397`，必须跨重跑保留）；
3. **审计历史**（`resolved`：旧 active 不再成立，`attribution.py:351`）。

诊断读的是 (1)，却用 (1)+(2) 混存的表，所以必须先打底把 (1) 写进去。

**附带发现（leverage）**：`run_attribution_for_student:276` 自己调 `assess_student_kps`，而 `generate_student_diagnosis:47` 也调了一次--**同一份评估算了两遍**。深化后可复用，顺手省掉重复计算。

### 修复设计：`resolve_attributions` 深模块

把「该生在该时点的归因」收进一个**只读**深模块，诊断生成器在内部调用它。接口简单、实现藏住「推导 ⊕ 叠加裁决」。

```python
# pipeline/attribution.py（新增；纯读，不写库）
@dataclass
class ResolvedAttribution:
    kp_id: int
    type: str
    confidence: float
    root_kp_id: int | None
    evidence: list[dict]
    prediction: str
    verdict: str            # "active" | "overridden"
    teacher_note: str | None

def resolve_attributions(
    session, graph, student_id, class_id, as_of, *,
    assessments=None, events_by_sk=None,
) -> list[ResolvedAttribution]:
    """该生该时点的归因（derive-on-read）：推导新鲜假设 ⊕ 叠加持久化的人工裁决。

    assessments 可由调用方传入（诊断已算过，避免重复）；缺省则内部 assess。
    不写库：即便 Attribution 表无 active 行，也能给出正确的（推导）归因。
    """
    assessments = assessments or assess_student_kps(
        session, graph, student_id, class_id, as_of, events_by_sk=events_by_sk
    )
    covered = covered_kp_ids(session, class_id, as_of)
    # 1) 纯推导（复用现有 attribute_assessment + 全局薄弱抑制逻辑）
    findings = _derive_findings(session, graph, student_id, assessments, covered, as_of)
    # 2) 加载人工裁决（仅 overridden；resolved/active 不参与读路径）
    verdicts = {
        (att.kp_id, att.type): att
        for att in session.scalars(
            select(Attribution).where(
                Attribution.student_id == student_id,
                Attribution.status == "overridden",
            )
        )
    }
    # 3) 叠加：被裁决否决的假设标 overridden 并附 note，否则 active
    out = []
    for f in findings:
        v = verdicts.get((f.kp_id, f.type))
        out.append(ResolvedAttribution(
            kp_id=f.kp_id, type=f.type, confidence=f.confidence,
            root_kp_id=f.root_kp_id, evidence=f.evidence, prediction=f.prediction,
            verdict=("overridden" if v else "active"),
            teacher_note=(v.teacher_note if v else None),
        ))
    return out
```

`_derive_findings` = 把 `run_attribution_for_student:279-308` 的推导部分（`attribute_assessment` 循环 + 全局薄弱抑制）抽成纯函数，**去掉落库**。

### 具体改动清单

| 位置 | 改动 |
|---|---|
| `pipeline/attribution.py` | 新增 `ResolvedAttribution` + `resolve_attributions` + `_derive_findings`（从 `run_attribution_for_student` 抽出推导部分） |
| `reports/student_diagnosis.py:50-58` | 删掉 `select(Attribution where status=active)`；改为 `attributions = {r.kp_id: r for r in resolve_attributions(session, graph, student_id, student.class_id, as_of, assessments=assessments, events_by_sk=events_by_sk)}`。`:145-164` 的渲染逻辑基本不动（`att.confidence/root_kp_id/evidence_json/prediction` 字段名对齐到 `ResolvedAttribution`；`evidence_json` → `evidence`） |
| `routes.py:842,853,861` | **删掉** `run_attribution_for_student(...)` 三处打底调用。`generate_student_diagnosis` 自己解析归因，不再依赖外部打底 |
| `auto_generate.py:105-106` | 把 `run_attribution_for_student(...)` 改名/保留为**显式物化**步骤（见下） |

### 写路径怎么办（不破坏 override/verify/closure）

override（`routes.py:1936`）、verify（`:1949` → `verify_attribution_prediction`）、closure（`:1975` → `attribution_closure`）都依赖**有持久化 `Attribution` 行（带 id）**可指可统计。所以不能完全停止写库。

把 `run_attribution_for_student` 的写库部分**重命名**为 `materialize_attribution_verdicts`，职责收窄为：

> 为已生成报告的归因物化行（供教师否决/证伪/闭合率统计），仍是 upsert + 保留 overridden + 旧 active→resolved。

调用时机：**报告生成成功后**（auto_generate、以及 diagnosis 端点的 generate 分支），作为“生成”的尾步，而非“读”的前置。语义从「打底才能读对」变成「生成时顺手物化，方便后续否决」。

- 若物化被跳过/失败：诊断渲染仍正确（resolve 推导），只是该报告的归因暂不可被 override-by-id--**优雅降级，而非静默错**。
- override/verify/closure 代码**零改动**：表结构、status 机、id 语义全不变。

> 未来可选（Phase 2，本方案不做）：把 override 改为按 `(student_id, kp_id, type)` 建裁决记录，彻底去掉 active 行缓存，全文 derive-on-read。需改 override 端点契约（id → key）与前端，风险更大，待 override 工作流有真实使用反馈再评估。

### TDD 测试计划

新接口即测试面。先 RED：

```
tests/test_attribution_resolve.py（新增）
- test_resolve_derives_fresh_without_priming
    零 Attribution 行 → resolve 仍返回推导出的归因（证伪“必须先打底”）
- test_resolve_overlays_overridden_verdict
    预置 overridden 行 → 对应假设 verdict=='overridden' 且带 teacher_note
- test_resolve_ignores_resolved_rows
    resolved 行不参与叠加（不复活已失效假设）
- test_resolve_reuses_passed_assessments
    传入 assessments → 不再二次 assess（mock assess_student_kps 计数=1）
- test_resolve_global_weak_suppression
    多数 kp 薄弱 → 前置缺陷 confidence 被封顶（迁移自现有抑制逻辑）
- test_diagnosis_no_longer_silent_without_priming
    集成：不调 run_attribution 直接 generate_student_diagnosis → 归因段正确渲染
```

迁移现有 `test_auto_report.py`：断言不依赖“先 run_attribution 后 generate”的顺序；改为直接断言诊断内容。`run_attribution_for_student` 的 upsert/override 保留测试迁到 `materialize_attribution_verdicts` 名下。

### 风险与回滚

- **风险**：`_derive_findings` 抽取时漏搬全局薄弱抑制（`:284-308`）→ 回归。对策：先写 `test_resolve_global_weak_suppression`（RED）再搬。
- **风险**：物化时机变化导致部分报告的 attribution 行缺失 → override 404。对策：物化仍在报告生成尾步同步执行，仅顺序调整，不延后。
- **回滚**：`resolve_attributions` 是新增；回滚只需还原 `student_diagnosis.py:50-58` 与三处打底调用。

---

## 候选 2 ｜routes.py 拆分 + 逻辑抽取

### 现状

`routes.py` 2048 行 / 56 端点，HTTP 胶水与业务逻辑混杂。删除测试：作为整体它是浅的--拆分只是**搬走**逻辑而非浓缩；但问题正在于内嵌逻辑没有“家”，只能经 HTTP 测。

### 拆分地图（按域 router + service/query 层）

把单 `router` 拆成按域的多个 router，在 `main.py` include；同时把内嵌逻辑抽进域模块，routes 退回薄适配器（parse → call → shape）。

| 新 router 文件 | 端点域 | 抽出的逻辑模块 |
|---|---|---|
| `api/routers/org.py` | schools/classes/students/progress/exams 列表与详情 | `queries/classes_overview.py`（`classes_overview` 78 行聚合，`:1540-1616`）、`queries/class_lists.py`（`list_classes` 的 count 聚合） |
| `api/routers/kb.py` | kb import/versions/kps/relations/export/suggest-tags | `kb/edit.py`（kp/relation CRUD 校验+级联：`create_kp/update_kp/delete_kp/create_relation/update_relation/delete_relation` 的非 HTTP 逻辑）、`kb/floor_impact.py`（`_weak_count_for_kp/_floor_impact` preview 影响估算）、`kb/compatibility.py`（`_compatibility`） |
| `api/routers/ingestion.py` | exams CRUD/excel/manual/photo/batch/commit/approve-tags/review-queue | `ingestion/batch_upload.py`（`_validate_and_persist/_cleanup_saved/_effective_sync` 文件策略，`:443-503`） |
| `api/routers/analysis.py` | mastery/weakness/attributions/diagnosis/quality-report/verify/override/closure | `reports/diagnosis_orchestrator.py`（diagnosis get-or-generate 三分支 + `get_or_create_narrative` + `_latest_stored_diagnosis`，`:790-871`） |
| `api/routers/reports.py` | reports 列表/详情 | （薄，直接查） |

`_active_kb`/`_graph`/`_as_dt`/`get_db` → `api/deps.py`（候选 5 顺带统一 `_active_kb`）。

### 抽取纪律

- 只抽**有逻辑**的端点；纯 3-6 行透传（如 `commit:261`、`approve_tags:414`）保持原样，避免为拆而拆制造浅模块。
- 抽出的 service/query 函数**不接收 `Depends`/`HTTPException`**：领域层抛 `ValueError`/领域异常，router 层翻译成 `HTTPException`。这是 seam 的体现--逻辑与 HTTP 解耦后可单测。
- `classes_overview` 抽出后，其 `try/except HTTPException` 兜底（active kb 缺失返回 `{0,0}`）应换成领域层返回 `None`，router 决定如何兜底。

### TDD 测试计划

```
tests/test_classes_overview.py   - 聚合待办数/最近考试/进度覆盖，纯函数测（不经 HTTP）
tests/test_kb_edit.py            - kp 软归档 confirm 守卫、硬删引用预检、relation 自环/跨版本校验
tests/test_batch_upload_policy.py - 文件大小/数量/类型校验、tempfile 清理、sync 策略
tests/test_diagnosis_orchestrator.py - 三分支 get-or-generate（与候选 1 协同）
```

### 风险

- 拆分面广、import 多。对策：**按 router 逐个迁**，每迁一个跑全量 `pytest tests simulator`。优先迁 `analysis`（承载候选 1）与 `org`（含 `classes_overview`）。
- 循环 import 风险（现有延迟导入 `routes.py:374/397/417/427/492/546/567` 多处）。对策：service 层不反向依赖 `api/`，延迟导入自然消失。

---

## 候选 3 ｜报告生成器分层

### 现状

`generate_quality_analysis`（`quality_analysis.py:32-254`）与 `generate_student_diagnosis`（`student_diagnosis.py:32-231`）各 ~220 行，串了四件事：算统计 / 拼 markdown / 落 Report / 调 LLM。渲染块无 DB+证据全 setup 不可测；`generate_quality_analysis` **无覆盖测试**。中途 `from app.reports.narrative import render_narrative`（`quality_analysis.py:225` / `student_diagnosis.py:195`）是未显式化的 seam。

**附带发现（bonus）**：`quality_analysis.py:70-80` 逐题×逐作答 `session.scalar(select(ResponseAnswer)...)` = Q×R 次 N+1 查询。分层时一并修成批量取（`select(ResponseAnswer).where(exam_response_id.in_(...))` 一次取全，按 `template_question_id` 分组）。

### 修复设计：compute / render / persist 分层

```python
# reports/quality_model.py（新增，纯）
@dataclass
class QualityReportModel:
    class_name: str; exam_name: str; exam_date: date
    committed: int; pending: int
    totals: list[float]; full_total: float
    question_rates: list[dict]
    kp_stats: dict[int, dict]
    common_weak: list[dict]

def compute_quality_model(session, graph, class_id, exam_id, *, events_by_sk=None) -> QualityReportModel:
    """纯计算：从证据/作答算统计。无 markdown、无落库、无 LLM。"""
    # 含 N+1 修复：批量取 ResponseAnswer

# reports/quality_render.py（新增，纯）
def render_quality_markdown(model: QualityReportModel) -> str:
    """模型 -> markdown（大段字符串拼接集中于此）。无 DB、无 LLM。"""

# reports/quality_analysis.py（瘦身后）
def generate_quality_analysis(session, graph, class_id, exam_id, *, narrative=False, events_by_sk=None) -> Report:
    model = compute_quality_model(session, graph, class_id, exam_id, events_by_sk=events_by_sk)
    md = render_quality_markdown(model)
    if narrative:
        md += narrate(model, "quality_analysis")  # 显式 seam，不再延迟 import
    return _persist_report(session, "quality_analysis", class_id=class_id, exam_id=exam_id,
                           snapshot=model_to_snapshot(model), markdown=md)
```

`student_diagnosis` 同构：`compute_diagnosis_model`（含候选 1 的 `resolve_attributions`）→ `render_diagnosis_markdown` → persist。归因段渲染从读 ORM `Attribution` 改为读 `ResolvedAttribution`（候选 1 已铺好）。

### narrative 纳入 LLM 闸门（强化不变量③）

现状：`narrative.py:23-34` 吞所有 `LLMError` 静默降级，无熔断、无重试；`circuit.py:77` 注释「文本报告叙述另需时再加一个」从未补上。文本路径无闸门，仅靠 prompt 自律（`prompts.py`「铁律」）。

修复：新增 `llm/gateway.py`（或扩 `circuit.py`），文本与视觉共用同一闸门接口：

```python
def narrate(model, report_type) -> str:
    """唯一文本 LLM seam：熔断 → 调用 → 校验 → 返回带标注段落；不可用返回 ''。"""
    _text_breaker.before_call()           # 新增文本熔断器
    try:
        client = get_client("text")
        payload = client.parse_json(NARRATIVE_SYSTEM, narrative_user_prompt(...), None)
        _text_breaker.record_success()
        return _format_section(_coerce_text(payload), client.model_version)
    except (LLMError, CircuitOpenError):
        _text_breaker.record_failure()
        return ""
```

`_format_section` 沿用现有「模型生成，数字以系统计算为准」标注（不变量④）。`narrative.py` 的 `render_narrative` 退为内部实现或合并入 `narrate`。

### TDD 测试计划

```
tests/test_quality_render.py    - render_quality_markdown 纯函数：空数据/低得分率/共性薄弱/未标注题
tests/test_quality_model.py     - compute_quality_model：N+1 修复后结果等价、events_by_sk 复用
tests/test_diagnosis_render.py  - 渲染成长框架顺序、归因段（ResolvedAttribution）、前向影响预警
tests/test_narrate_gateway.py   - 熔断 open→fast-fail、LLMError 降级为 ''、成功带标注
```

### 风险

- 拆分时 snapshot_json 字段需保持前端契约（`Diagnosis.tsx` 读 `snapshot.as_of/weak/attributions`）。对策：`model_to_snapshot` 显式对齐现有字段，加 snapshot 形状快照测试。
- 与候选 1 有耦合：诊断模型层依赖 `resolve_attributions`。先做 1 再做 3 的诊断部分。

---

## 候选 4 ｜commit_exam seam 显式化

### 现状

`commit_exam`（`ingestion/commit.py:37-78`）用 `generate_reports: bool`（`:38/:74`）给函数两套语义：True=生产（提交后自动生成报告），False=模拟脚本（`run_demo`/`effectiveness_*`/`diagnose_root_causes`）。报告步骤拴在 commit 上而非被组合。

### 修复设计

```python
# ingestion/commit.py
def commit_exam(session, template_id) -> CommitResult:
    """只做不变量①：状态机迁移 + 派生证据 + 题库飞轮。不再生成报告。"""
    # 删掉 generate_reports 参数与 :74-77 的报告分支
    ...

# API 端点（routes.py commit endpoint）= 产品语义「提交即自动生成」
def commit(exam_id, db):
    result = commit_exam(db, exam_id)
    if result.committed_responses > 0:
        reports = generate_exam_reports(db, exam_id)   # 显式组合，best-effort
        result.quality_report = reports.quality
        result.diagnoses = reports.diagnoses
    return result

# 模拟脚本 = 只 commit
commit_exam(session, template_id)   # 不再传 generate_reports=False
```

产品行为不变（端点仍“提交即生成”），但 seam 显式化：报告生成是调用方组合的一步，而非 bool 切换的隐藏语义。`generate_reports` 参数与 `CommitResult.quality_report/diagnoses` 字段可保留（端点回填）或随清理移除。

**注**：题库飞轮 `seed_bank_from_template` 留在 commit 内（紧耦合“题目随提交即审核入库”，属采集语义）。若后续要进一步解耦，可同理抽出，但本方案不做。

### TDD 测试计划

```
tests/test_commit.py            - commit 不再触发报告生成；状态机+证据+飞轮行为不变
tests/test_auto_report.py       - 端点级：commit 后报告确实生成（迁移自现有）
simulator/test_gold.py 等       - 脚本去掉 generate_reports=False 后全绿
```

### 风险

- 多个脚本传 `generate_reports=False`，需同步改。对策：保留 `generate_reports` 参数一段时间并 `DeprecationWarning`，或一次性改全（脚本数有限：`run_demo`/`effectiveness_multiround`/`effectiveness_largescale`/`diagnose_root_causes`/`mock_photo_run`）。

---

## 候选 5 ｜去重（零风险铺垫）

### 5a. 统一 `_active_kb`

两份实现行为分叉：
- `routes.py:95`：无 active 时按 `SC_KB_STRICT_ACTIVE` 决定抛 `HTTPException` 或兜底最新（带 warning）。
- `auto_generate.py:120`：无 active 时返回 `None` + warning，无 strict。

修复：抽 `kb/resolver.py`，领域层返回 `KbVersion | None` 并在 strict 模式抛**领域异常**（如 `KbNotActiveError`）；router 层 catch → `HTTPException`，报告层拿 `None` 跳过。一处策略，两处消费。

```python
# kb/resolver.py
class KbNotActiveError(RuntimeError): ...

def active_kb(session) -> KbVersion | None:
    kb = session.scalar(select(KbVersion).where(KbVersion.status=="active").order_by(KbVersion.id.desc()))
    if kb: return kb
    if strict_active():               # 读 SC_KB_STRICT_ACTIVE
        raise KbNotActiveError("无 active 知识库版本")
    kb = session.scalar(select(KbVersion).order_by(KbVersion.id.desc()))
    if kb and kb.status != "active": warn(...)
    return kb

# api/deps.py
def require_active_kb(session) -> KbVersion:
    try: return active_kb(session)
    except KbNotActiveError as e: raise HTTPException(400, str(e))
```

### 5b. 标签 codegen（drift 已现）

**drift 证据**：`ATTR_CONFUSABLE = "易混淆"`（`attribution.py:49`，kb-improvement K1 新增）在 `labels.py:10` 的 `ATTR_LABEL` 与 `labels.ts:9` 的 `ATTR_LABEL` **双双缺失** → 该归因类型在报告与界面都原样显示「易混淆」而非口语标签。这正是“同一映射两处写”的漂移，已经发生。

修复：单一真源 + codegen。推荐方案--

- 新增 `backend/app/labels_source.py`（或 YAML）：单一字典 `ATTR_LABEL / TRAJ_LABEL / CRITERION_LABEL / BAND_LABEL / VERSION_STATUS_LABEL`。
- `reports/labels.py` 从 source 导入（后端直接用）。
- 新增 `scripts/gen_labels_ts.py`：读 source → 生成 `frontend/app/src/lib/labels.ts`。接入 `package.json` prebuild 或 `make`。
- 测试：`test_labels_sync.py` 断言 codegen 产物与 source 一致（防漂移再发）。

> 备选：`GET /meta/labels` 端点供前端运行时拉取。运行时依赖、离线不可用，不推荐。

### TDD 测试计划

```
tests/test_kb_resolver.py   - strict 开/关行为、兜底 warning
tests/test_labels_sync.py   - source ↔ labels.py ↔ labels.ts 三方一致；新枚举不漏
```

---

## 实施顺序与依赖

```
5a 统一 _active_kb ─┐
5b 标签 codegen ────┤ (零风险铺垫，可并行)
                    ↓
1  resolve_attributions（恢复不变量②）──┐
                                       ↓
3  报告分层（compute/render/persist + narrative 闸门）──┐
                                                       ↓
2  routes.py 按域拆 router + 抽逻辑（给 1/3 的模块安家）──┐
                                                         ↓
4  commit_exam 去 bool（最后，脚本多处）
```

- 1 与 2 高度协同：`reports/diagnosis_orchestrator.py`（候选 2）正好装入 `resolve_attributions`（候选 1）。建议同一 PR 推进 diagnosis 这条线。
- 每步独立 PR、独立可回滚；每步后跑 `cd backend && python -m pytest tests simulator`（150 项基线全绿）。

## 不做的事（out of scope）

- 不改 `Attribution` 表结构与 status 状态机（override/verify/closure 零影响）。
- 不动已深的模块：`evidence.py`/`mastery.py`/`weakness.py` 的纯推导、`llm/client.py` 的 provider 适配器（已是真 seam，两个适配器=真 seam）。
- 不做 Phase 2 的 override-by-key 改造（待真实使用反馈）。
- 不引入新框架/新依赖（codegen 用纯 Python 脚本）。

## 附：建议建立 `CONTEXT.md`

项目无 `CONTEXT.md`，域词散在 README。本方案引入一个新深模块概念「**归因解析**（Attribution Resolution）」--`resolve_attributions`。建议落词：

```markdown
## 归因解析（Attribution Resolution）
为某生在某时点解析其归因集合的过程：从证据实时推导假设（前置缺陷/遗忘/易混淆/数据不足），
再叠加持久化的人工裁决（教师否决/诊断题证伪）。读路径 derive-on-read（不变量②），
不依赖外部“打底”。裁决记录单独持久化，供 override/verify/closure 工作流。
```

后续架构评审（含本 skill 复跑）将以 `CONTEXT.md` 为域词来源。
