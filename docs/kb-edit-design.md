# 知识库查看与编辑 - 设计文档 v0.2

> 状态：待评审 · 所在目录：项目根（与 backend/ frontend/ 同仓，方便对照）
>
> 版本演进：
> - v0.1：初版，补齐"浏览 + 教学进度全管理 + 知识点/关系 CRUD + 版本管理 + YAML 导出"。
> - v0.2：实际使用风险评估后补 4 处--归档预检纳入 `question_kp` 引用、属性改动影响预览、版本切换加属性 diff、切换快照与结构回滚（以 **〔v0.2〕** 标注）。
>
> 范围：前端目前只能一次性导入知识库（Wizard），导入后**全平台无法查看/编辑**。本设计补齐"浏览 + 教学进度全管理 + 知识点/关系 CRUD + 版本管理 + YAML 导出"。
>
> 决策默认（评审可调）：
> - 删除策略：**软归档**（`KnowledgePoint.archived`，被引用的 kp 归档不删行）
> - 版本切换：**超集约束**（新 active 的 code 集合须 ⊇ 旧 active，防旧证据静默丢失）
> - 关系版本隔离：**不加 schema**，靠端点 kp 隐式归属（`graph.py` 已按 kp 集合过滤）
> - UI 编辑**不回写 YAML**；YAML 仅作初始导入与结构大改来源；导出从 DB 现状生成

---

## 1. 背景与目标

| 现状痛点 | 本次目标 |
|---|---|
| 导入后看不到知识库全貌 | 知识库页：章节树 + 详情（属性/前置链/后继） |
| 教学进度只能增、不能删/改日期 | 教学进度增/删/改日期 |
| mastery_floor 等参数无法调 | 知识点属性微调（留痕） |
| 增删知识点/改关系只能改 YAML 重导 | UI 内增删知识点、改关系 |
| 只有一个隐式 active 版本，无管理 | 版本列表 + 切换 active（带兼容校验） |
| 无法导出当前知识库 | 导出 YAML（从 DB 生成） |

## 2. 现状（对照实际代码）

**后端 API（routes.py）**：
- `POST /kb/import`（路径）、`POST /kb/upload`（文件）-> `import_kb`（loader.py）
- `GET /kb/kps` -> 仅 code/name/chapter/grade 四字段，**不含** description/cog_levels/mastery_floor/difficulty_prior/semester/archived
- `POST /classes/{id}/progress`（增）、`GET /classes/{id}/progress`（列）-> **无删/改**
- `_active_kb`（routes.py:72）= `select(KbVersion).order_by(id.desc()).first()` -> **不看 status**，最新版本即 active（即便 status=draft）

**loader.py 关键不变量**：
- 同 subject+edition+version 且 code 集合相同 -> 幂等返回既有 kb_version（不更新属性）
- code 集合变化 -> 建新 kb_version
- `KbVersion.status`：draft|reviewed|active（字段已存在，但 `_active_kb` 没用它）

**引用关系（编辑的风险根源）**：
- `evidence_event.kp_id` -> KnowledgePoint.id（已提交证据，不可变追加）
- `question_kp.kp_id` -> KnowledgePoint.id（题目标注）
- `KpRelation` **无 kb_version_id**：靠 from_kp_id/to_kp_id 隐式归属版本；`graph.py` 加载时取"两端点都在当前版本 kp 集合"的 prerequisite 边
- `graph.kp(kp_id)` 跨版本兜底（按主键回查）-> 防 KeyError，但**不防"统计遗漏"**（旧证据指向新版本没有的 code 时，`grade7_kp_ids()` 不含它 -> 从分析静默消失）

## 3. 数据模型变更

### 3.1 `KnowledgePoint` 加 `archived` 字段

```python
archived: Mapped[bool] = mapped_column(default=False)  # 软归档：分析层排除，不删行
```

**迁移问题**：`create_all` 只建缺失的表，**不给已有表加列**。存量库需一次性迁移：
- 提供 `backend/scripts/migrate_kb_archived.py`：`ALTER TABLE knowledge_point ADD COLUMN archived BOOLEAN DEFAULT 0`
- 或重建库（MVP 可接受，但会丢证据 -> 不推荐对在用库）
- 迁移脚本幂等（检查列是否存在再 ALTER）

**分析层排除 archived**：
- `graph.grade7_kp_ids()`：加 `.where(KnowledgePoint.archived.is_(False))`
- `KpGraph.__init__` 加载 kp 时排除 archived（或加载但标记）
- 教学进度勾选、审核台闭集选择器、质量分析 kp 遍历均排除 archived
- **已派生的 `evidence_event` 不动**（历史证据保留，archived kp 的旧证据仍可按主键回查，只是不进新分析）

### 3.2 `_active_kb` 改为按 status 取（**行为变更，需迁移**）

```python
def _active_kb(session) -> KbVersion:
    kb = session.scalar(select(KbVersion).where(KbVersion.status == "active").order_by(KbVersion.id.desc()))
    if kb is None:
        # 兜底：无 active 时取最新并提示（避免老库升级即报错）
        kb = session.scalar(select(KbVersion).order_by(KbVersion.id.desc()))
    return kb
```

**迁移**：上线时把当前最新 kb_version 置 `status="active"`（一次性脚本，同 `migrate_kb_archived.py`）。否则改 `_active_kb` 后老库找不到 active 版本。

### 3.3 不加 schema 的部分

- `KpRelation` 不加 `kb_version_id`：靠端点 kp 隐式隔离（见 §6.3）。新增/改关系时校验 from/to 都属于目标版本。
- 不加 `KbVersion.is_active` 字段：用 `status="active"` 表达。

## 4. 后端接口（新增/扩展）

### 4.1 浏览（只读）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/kb/versions` | 列全部版本：id/subject/textbook_edition/version/status/created_at/kp_count/is_active |
| GET | `/kb/kps` | **扩展**：返回完整字段（+description/cog_levels/mastery_floor/difficulty_prior/semester/archived）；支持 `?kb_version_id=` 查指定版本（缺省 active） |
| GET | `/kb/kps/{kp_id}` | 单 kp 详情 + 前置链（prerequisite_chain）+ 后继（谁以它为前置）+ contains 关系 |
| GET | `/kb/relations?kb_version_id=` | 关系列表：from_code/to_code/type/weight（按端点 kp 归属版本过滤） |

### 4.2 教学进度（增删改）-- **本期优先**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/classes/{class_id}/progress` | 既有（增），保持 |
| DELETE | `/classes/{class_id}/progress/{kp_id}` | **新增**：取消已教标记 |
| PATCH | `/classes/{class_id}/progress/{kp_id}` | **新增**：改 taught_at（body: {taught_at}） |

校验：kp 属于 active kb；archived kp 不允许新标记（既有标记可删）。

### 4.3 知识点 CRUD

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/kb/kps` | 新建：code/name/grade/chapter/cog_levels/mastery_floor/difficulty_prior/semester/description。**code 同版本唯一**校验（uq_kb_code 已有约束 + IntegrityError 兜底）。新建的 kp 属于 active kb。 |
| PATCH | `/kb/kps/{kp_id}` | 改 name/description/chapter/cog_levels/mastery_floor/difficulty_prior/semester/archived。**不允许改 code**（稳定标识；改 code 走导出YAML->改->重新导入）。留痕 CorrectionLog。**〔v0.2〕** 改 `mastery_floor`/`difficulty_prior` 属高杠杆参数，支持 `?preview=true` 干跑预览（不落库）。 |
| DELETE | `/kb/kps/{kp_id}` | **软归档**：置 archived=True。见 §5 引用预检。 |

**〔v0.2〕属性改动影响预览（防"参数即结论"静默翻转）**：
`mastery_floor`/`difficulty_prior` 是分析高杠杆参数，derive-on-read 即时生效。教师随手一改可能让一批学生新增/退出"薄弱"。故：
- `PATCH /kb/kps/{id}?preview=true`：不落库，返回 `{current: {weak_count, floor}, projected: {weak_count, floor}, delta}`，其中 weak_count = 该 kp 所属班级中 mastery < floor 的学生数（按当前 vs 预期 floor 各算一次）。
- 前端详情面板改 floor 时实时调 preview 显示"当前 N 人薄弱，改后预计 M 人（Δ +K）"，教师确认后才真正 PATCH。
- 非 preview 的 PATCH 落库后，返回体也带 projected 影响数，便于留痕。
- 报告生成时在页脚标注"本报告基于知识库 v[X]（最后改动于 Y）"，让结论可追溯到知识库版本。

### 4.4 关系 CRUD

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/kb/relations` | 新建：from_kp_id/to_kp_id/type/weight。校验 type ∈ {prerequisite,contains,confusable,spiral}、weight∈[0,1]、**两端点同属 active kb**、非自环。 |
| PATCH | `/kb/relations/{id}` | 改 type/weight。 |
| DELETE | `/kb/relations/{id}` | 删。 |

### 4.5 版本管理

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/kb/versions` | fork 当前 active：复制其 kp（含 archived）+ 关系为草稿新版本（status=draft）。返回新 id。 |
| PATCH | `/kb/versions/{id}` | 改 status：draft->reviewed->active。**切 active 前做超集校验**（§6.2）。 |
| GET | `/kb/versions/{id}/compatibility` | 与当前 active 的 code 差集：{missing_codes, new_codes}。切换前预览。 |

### 4.6 导出

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/kb/export?kb_version_id=` | 从 DB 现状生成 YAML（对齐 kb.yaml 结构：meta/knowledge_points/relations），`Content-Disposition: attachment`。 |

导出 YAML 结构（对齐 loader 能读回）：
```yaml
meta: {subject, textbook_edition, version, status}
knowledge_points:
  - {code, name, description, grade, semester, chapter, cog_levels_expected, difficulty_prior, mastery_floor, is_container}
relations:
  - {from, to, type, weight}
```
`is_container` 按 `code.startswith("C")` 推导（模型无此字段）。archived kp 导出时加 `archived: true`（loader 导入时识别并置 archived）。

## 5. 引用完整性与软删除

**删 kp（DELETE -> 软归档）流程**：
1. 预检三类引用：`evidence_event.kp_id` 计数（已提交证据）、`question_kp.kp_id` 计数（题目标注）、`TeachingProgress.kp_id` 计数（教学进度）。
2. **默认软归档**（archived=True），不删行。返回 `{archived: true, evidence_refs: N, question_refs: M, progress_refs: K}`。
3. **〔v0.2〕归档对题目标注的影响**：归档后 kp 不进 `grade7_kp_ids()`，但引用它的 `question_kp` 还在 -> 质量分析"逐题->kp"映射时，标着该 kp 的题目成绩会丢失/断链。故：
   - `question_refs > 0` 时归档**不直接生效**，返回 409 + 引用清单，前端弹窗"该 kp 被 N 道题标注，归档后这些题目的知识点分析将缺失"，教师勾选"我已知晓"后带 `confirm=true` 重新调用才归档。
   - 归档后这些 `question_kp` 标注**保留不动**（不删），但分析层把它们记为"归属已归档 kp，未计入"并在报告里列出"N 道题的知识点归属已失效"，让缺失可见而非静默。
4. **〔v0.2〕归档时清理教学进度残留**：归档即删该 kp 的 `TeachingProgress` 记录（避免 Overview"已标记 N 个"含归档 kp 的困惑），返回里列出已清理的 progress_refs。恢复（archived=False）不自动重建进度。
5. 教师可在 UI 看到引用计数；archived kp 灰显，可"恢复"（PATCH archived=False）。
6. **硬删（force=true）**：仅当 evidence_refs=0 且 question_refs=0 时允许；级联删端点为该 kp 的 KpRelation；删 TeachingProgress 中该 kp 的记录。被引用时 force=true 也拒绝（400）。

**改 code 不支持**：PATCH 不暴露 code 字段。改 code 会破坏 YAML 幂等匹配键 + 让旧 code 的外部引用困惑。需求走导出YAML->改code->作为新版本导入。

**改关系**：关系是分析层 derive-on-read 的输入（归因 prerequisite_chain），改关系立即影响归因结论，不破坏数据。低风险。

## 6. 版本切换与一致性

### 6.1 切换 active 的语义

`PATCH /kb/versions/{id}` status->active 时：
1. 把当前 active 版本置 reviewed（或保留 active？设计：同时只一个 active，旧的降为 reviewed）。
2. 目标版本置 active。
3. 之后所有 `_active_kb` 取它。

### 6.2 超集约束 + 属性 diff（防旧证据静默丢失 / 防参数劣化）

切换前两道校验：

**① code 超集**：目标版本 code 集合（含 archived）必须 **⊇ 当前 active code 集合**。
- 缺失 code -> 400，报告 `{missing_codes: [...]}`，提示"旧证据指向这些 code，切换后会从分析消失"。
- 教师可选择：在目标版本补齐缺失 code（新建同名 kp），或先归档/接受丢失（需 force=true + 二次确认）。

**〔v0.2〕② 属性 diff**：超集只防 code 缺失，**不防参数劣化**（如把所有 mastery_floor 改 0.3 让全班瞬间"全薄弱"，超集仍满足）。故切换前额外 diff 同 code kp 的高杠杆属性变化：
- `GET /kb/versions/{id}/compatibility` 返回扩展为 `{missing_codes, new_codes, attribute_changes: [{code, field, old, new}, ...]}`，只 diff `mastery_floor`/`difficulty_prior`/`archived`（影响分析结论的字段）。
- `PATCH /kb/versions/{id}` 切 active 时，若 `attribute_changes` 非空，返回 409 + 变更清单，前端弹窗"切换将改变 N 个知识点的高杠杆参数，分析结论会改变"，教师 `confirm=true` 后才执行。
- 单纯 code 超集通过 + 无属性变化 -> 直接切换，无需 confirm。

### 6.3 关系的版本隔离（不加 schema）

`KpRelation` 无 kb_version_id，靠端点 kp 隐式归属：
- 查询：`GET /kb/relations?kb_version_id=X` -> 取端点 kp 都属于版本 X 的关系。
- 新建/改关系：校验 from_kp_id、to_kp_id 都属于目标版本（active 或指定）。
- fork 版本时：复制关系时把端点 kp_id 映射到新版本的对应 kp_id（按 code 匹配）。

### 6.4 UI 编辑与 YAML 导出/导入一致性

- **导出**：从 DB 现状生成（反映 UI 增改），不返回原文件。
- **导入**：复用 `import_kb` 幂等逻辑（同 subject+edition+version+code 集合 -> 返回既有）。但 UI 编辑后的属性不会被导入覆盖（import_kb 不更新属性）。
- **语义**：UI 编辑 = 运行时覆盖；YAML = 初始来源 + 结构大改出口。两者并存，导出 YAML 是"当前 DB 快照"，可作为下次导入的基线。
- **坑**：UI 改了属性后，若用原 YAML 重新导入（同 code 集合），幂等返回既有版本，**UI 改动保留**（不被覆盖）。这是期望行为。若想让 YAML 的属性生效，需改 version 号建新版本。

### 6.5 切换快照与结构回滚（〔v0.2〕）

**问题**：版本切换不是无损可逆--切到 B 后若有新考试提交（证据派生到 B 的 kp），切回 A 时这些新证据失联。教师以为"切错了切回去就行"，实际回不去。

**设计**：
- 切换 active 时写一条切换日志（复用 CorrectionLog：entity_type="kb_version"，field="active"，old=from_version_id，new=to_version_id）。前端版本历史可显示"v1 -> v2，由 X 于 Y 切换"。
- **结构回滚 = 切回旧版本**：旧版本切换后 status 降为 reviewed 但**不删**，教师可随时再 PATCH 切回。回滚走超集+属性 diff 同样校验（旧版本 code 通常 ⊇ 新版本？不一定，需校验）。
- **不可回滚的部分明确告知**：切换期间新派生的 `evidence_event` 留在新版本 kp 上，切回旧版本后这些证据不进分析。前端切换确认弹窗必含一句"切换后新产生的考试证据无法迁回旧版本"。
- **原地属性改动的回滚**：不靠版本（PATCH 在 active 版本原地改），靠 CorrectionLog 的 old/new。前端在 kp 详情面板提供"改动历史"列表 + 一键恢复某次改动的 old 值（即反向 PATCH）。

**不做的**：不为每次属性改动 fork 版本（工程量大、`_active_kb` 切换频繁），CorrectionLog 留痕 + 一键恢复足够 MVP。

## 7. 前端交互

### 7.1 路由

- **`/kb`**（全局，不绑班级）：知识库管理页。根布局外新增路由（与 `/wizard` 同级，不在 `/c/:classId` 下，因为知识库是全局的）。
- **教学进度**：在 `/c/:classId` 下，Overview 扩展或新增 `/c/:classId/progress` 页（按班级管理已教清单）。

### 7.2 知识库页（`/kb`）

- **顶部**：版本选择器（标 active）+ 「新建版本(fork)」+ 「导出 YAML」+ 「导入 YAML」+ 「切换 active」按钮。
- **左栏**：章节树（按 chapter 分组，archived kp 灰显折叠），「新建知识点」按钮。
- **右栏**：选中 kp 详情面板：
  - 属性表单（name/description/chapter/cog_levels/mastery_floor/difficulty_prior/semester）可编辑 + 「保存」
  - 前置链列表（prerequisite，含 weight）+ 「添加前置」+ 删除
  - 后继列表（谁以它为前置）
  - contains 关系（所属章节容器）
  - 引用计数显示 + 「归档」/「恢复」/「硬删（无引用时）」
- **版本切换**：点「切换 active」-> 先调 compatibility 预览缺失 code -> 确认 -> PATCH。
- **关系编辑**：在 kp 详情面板内增删该 kp 的关系（from/to 选同版本 kp）。

### 7.3 教学进度编辑（班级页）

- Overview「教学进度」卡片升级：显示已教清单（kp code/name + taught_at + 删除按钮），「添加已教」下拉（选 active kb 非 archived kp + 日期）。
- 或独立 `/c/:classId/progress` 页（列表更全）。
- 删除/改日期即时调 DELETE/PATCH。

### 7.4 状态/权限提示

- archived kp 在选择器（进度勾选、题目标注闭集）中不出现。
- 已提交证据引用的 kp 在详情面板标"已被 N 条证据引用，仅可归档"。
- 版本切换面板标"切换后分析层基于新版本，旧证据可能失联"。

## 8. 测试

**单元/接口测试**（新建 test_kb_edit.py，复用 conftest 的 session/env 夹具）：
- 浏览：`GET /kb/kps` 返回完整字段；`GET /kb/kps/{id}` 含前置链
- 教学进度：DELETE 取消、PATCH 改日期、再 GET 确认
- 知识点 CRUD：新建（code 重复 400）、PATCH 改属性（留痕 CorrectionLog）、DELETE 软归档（archived=True，分析层排除）
- 硬删：无引用 force=true 成功；有引用 force=true 仍 400
- 关系 CRUD：新建（端点跨版本 400、自环 400、非法 type 400）、PATCH、DELETE
- 版本：fork 新版本（kp/关系复制）、compatibility 报告 missing/new、切换超集校验（缺失 code 400）
- 导出：`GET /kb/export` 返回 YAML，结构可被 `import_kb` 读回
- 既有 56 测试全过（`_active_kb` 改动 + archived 排除不破坏既有路径）

**迁移测试**：`migrate_kb_archived.py` 在存量库幂等执行（列已存在不报错）。

**真实冒烟**：导入 kb.yaml -> UI 改一个 mastery_floor -> 归档一个无引用 kp -> fork 版本 -> 加一个 kp -> 导出 YAML -> 改 code 后作为新版本导入 -> 超集校验切换 active -> 生成报告确认分析层用新版本。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 删 kp 悬空证据 | 软归档不删行；硬删需无引用 + force |
| 改 code 破坏幂等匹配 | PATCH 不暴露 code；改 code 走导出YAML->新版本 |
| 切换 active 致旧证据静默丢失 | 超集约束 + compatibility 预览 + force 需二次确认 |
| `_active_kb` 改按 status 取，老库无 active | 迁移脚本把最新版本置 active；_active_kb 兜底取最新 |
| `archived` 加列，存量库不生效 | 迁移脚本 ALTER TABLE（幂等） |
| 关系无 kb_version_id 跨版本污染 | 端点 kp 隐式隔离；新建/改校验同版本；fork 按 code 映射端点 |
| UI 编辑与 YAML 导入优先级混乱 | 明确语义：UI=运行时覆盖，YAML=初始/大改；导出从 DB 生成 |
| archived kp 的旧证据/题目标注 | 旧证据按主键可回查但不进新统计；题目标注失效在报告里列出（可见非静默）；归档即清 TeachingProgress |
| 工程量大 | 分期：§4.2 教学进度优先 -> §4.1 浏览 -> §4.3 属性+归档 -> §4.4 关系 -> §4.5 版本 -> §4.6 导出 |
| **〔v0.2〕** 参数即结论静默翻转（改 mastery_floor 致报告突变） | PATCH `?preview=true` 影响预览（N 人薄弱->M 人）+ 报告标注知识库版本 |
| **〔v0.2〕** 超集虚假安全感（超集通过但属性劣化） | 切换加属性 diff + confirm；compatibility 返回 attribute_changes |
| **〔v0.2〕** 归档致题目标注成绩丢失 | 归档预检 question_kp，有引用 409 + confirm；报告列"失效题" |
| **〔v0.2〕** 切换不可逆，新证据无法迁回 | 切换日志（CorrectionLog）+ 结构回滚切旧版本 + 弹窗明示新证据不可迁 |
| **〔v0.2〕** 容器节点（C*）误删致章节树乱 | C* 前缀禁止删除/改 code，UI 标"容器节点仅可改名" |
| **〔v0.2〕** 多人并发改同 kp 后写覆盖 | MVP：CorrectionLog 留痕 + 详情显示最后修改人/时；P1 加 updated_at 乐观锁 |
| **〔v0.2〕** 迁移顺序错致全站 500 | `_active_kb` 兜底（无 active 取最新+告警）；迁移脚本先于代码上线；测试库验证 |

## 10. 实施顺序

1. **迁移**：`migrate_kb_archived.py`（加 archived 列 + 置最新版本 active）+ `_active_kb` 改按 status 取（含兜底）。
2. **后端 §4.2 教学进度**（DELETE/PATCH）+ §4.1 浏览（扩展 /kb/kps、/kb/kps/{id}、/kb/relations、/kb/versions）。
3. **后端 §4.3 知识点**（POST/PATCH/DELETE + 引用预检 + 软归档）+ §4.4 关系（CRUD + 同版本校验）。**〔v0.2〕** 含 PATCH `?preview=true` 影响预览、归档预检 `question_kp`+清 TeachingProgress+409 confirm。
4. **后端 §4.5 版本**（fork/compatibility/切换超集+**〔v0.2〕**属性 diff+confirm+切换日志）+ §4.6 导出。**〔v0.2〕** 结构回滚（切回旧版本）走同一 PATCH。
5. **前端**：`/kb` 知识库页（章节树+详情+CRUD+**〔v0.2〕**属性预览/改动历史一键恢复）+ 教学进度编辑（Overview 扩展）+ 版本切换 UI（**〔v0.2〕** compatibility 属性 diff 展示 + 切换确认弹窗 + 归档 409 二次确认）。
6. **测试**：test_kb_edit.py（含 **〔v0.2〕** preview 影响数、归档 question_kp 409、切换属性 diff、结构回滚、容器禁删）+ 迁移测试 + 既有回归 + 真实冒烟。
7. **文档**：DESIGN.md §4/§10 补"知识库编辑"小节。

> 建议先做 1+2+5(教学进度部分)，解决"看不到 + 进度不能改"的最直接痛点，再迭代 3-4-5(知识库页)。**〔v0.2〕** 属性预览与归档预检随 §3 一起做（否则编辑功能上线即埋雷）。
