# 分析归因系统设计缺陷改进 - 设计文档 v0.1

> 状态：待评审 · 所在目录：项目根（与 `backend/` `frontend/` 同仓，方便对照）
>
> 范围：基于对知识库层 / 采集层 / 追踪层 / 归因层实际代码的审查，梳理当前已实现部分的**结构性缺陷**与改进方向。本文是对 [DESIGN.md](DESIGN.md) v0.3 已落地部分的缺陷复盘，**不改 DESIGN 的能力边界与四条不变量**，而是补强建模与实现。
>
> 与已有设计稿的关系：[kb-edit-design.md](kb-edit-design.md) 已处理"导入后不可编辑"；[batch-photo-input-design.md](batch-photo-input-design.md) 已处理"批量拍照录入"。本文聚焦**分析正确性**，是前三份的下游。
>
> 决策默认（评审可调）：
> - 失分归属：**短期修正建模语义 + 中期引入诊断题探针**（非 IRT 重构）
> - 知识库版本：**收紧 `_active_kb`，draft 不再兜底当 active**
> - prerequisite weight：**让 weight 真正参与归因**（不删除，保留语义）
> - 标注质量：**建立真实人工金标**（200 题），作为精度生死的唯一观测手段

---

## 落地进度（v0.1 阶段一 · 已实现）

| 缺陷 | 状态 | 落地点 | 验证 |
|---|---|---|---|
| §2.2 图谱可疑边反查 | ✅ 已落地 | `kb/graph.py:suspect_edges` + `scripts/audit_kb_edges.py` + `tests/test_kb_audit.py` | 新增 2 测试；确定性构造无关边(corr=0)被标、完全正相关边(corr=1)不标 |
| §2.3 weight 参与归因 | ✅ 已落地 | `pipeline/attribution.py:_root_score`（缺口×(0.5+0.5·w)），evidence 透出 `edge_weight` | 金标根源命中维持 0.96，不退化 |
| §2.1 `_active_kb` 收紧 | ✅ 已落地（务实版） | `routes.py:_active_kb` 加 `SC_KB_STRICT_ACTIVE` 开关 + draft 兜底 WARNING | 默认行为不变（38 测试绿）；开启开关则无 active 报错 |
| §1.4-C 失分归属混合度折扣 | ✅ 已落地（开关） | `pipeline/evidence.py` + `config.py:EVIDENCE_MIX_PENALTY`，默认 0 关闭 | 默认 95 测试全绿；`=1` 时金标不退化（召回 0.77／命中 0.96／误报 0.21） |
| §6 题库飞轮 | ✅ 已落地 | `ingestion/commit.py:seed_bank_from_template`（提交考试时将有标注题目幂等写入 `bank_question`） | 新增 1 测试；全量 96 passed |
| §3.2 高置信抽样复核 | ✅ 已落地（灰度开关） | `SC_TAG_REVIEW_SAMPLE_RATE`；低置信永不批量过，高置信按稳定哈希抽样；前端显示复核原因 | 新增 1 测试；photo 18 passed；前端 build 成功 |
| §3.3 手工建卷 LLM 辅助标注 | ✅ 已落地 | `POST /kb/suggest-question-tags`（`templates.py:suggest_question_tags`，纯文本闭集推荐，不落库）；前端 ExamNew 5 列格式 + AI 推荐按钮 | 新增 1 测试；全量 98 passed；前端 build 成功 |
| §1.4-A 诊断题证伪闭环 | ✅ 已落地 | `pipeline/attribution.py:verify_attribution_prediction` + `POST /attributions/{id}/verify`；诊断题(单 kp)证据验证前置缺陷预测，证伪->`overridden`(跨重跑保留)、证实->记录确认、证据不足->inconclusive | 新增 3 测试；全量 101 passed |

**金标实测（改动后 · 默认配置）**：薄弱召回 0.80 · 根源命中 0.96 · 遗忘 3/3 · 共性 1.00 · 误报 0.21 - 与改动前一致，无退化。全量 101 passed（原 93 + 新增 8）。

**仍未落地**：§3.1 真实人工金标（200 题，需人工标注，是精度可观测的唯一手段）；§3.3 标注一致性提示（某 kp 高频修改则提示定义模糊）。

---

## 0. 问题总览（优先级矩阵）

| # | 缺陷 | 所在层 | 严重度 | 优先级 | 代码定位 | 阻塞核心价值？ |
|---|---|---|---|---|---|---|
| 1 | 失分归属不可解：`value` 无差别分摊给所有标注 kp | 追踪层 | 致命 | **P0** | `evidence.py:81-97` | 是（归因精度上限） |
| 2 | draft 草图兜底当 active，分析跑在未审图谱上 | 知识库层 | 致命 | **P0** | `routes.py:93-106` | 是（地基） |
| 3 | 图谱 prerequisite 边零校验（可疑边反查未实现） | 知识库层 | 高 | **P0** | DESIGN §4.4 未实现 | 是（归因依赖边） |
| 4 | 题-kp 标注质量不可观测（无真实金标） | 采集层 | 致命 | **P0** | 全局 | 是（精度不可知） |
| 5 | prerequisite `weight` 是死字段，建了不用 | 归因层 | 中 | **P1** | `attribution.py:104`、`graph.py:73` | 否（但误导） |
| 6 | 高置信标注静默通过，系统性标错不可见 | 采集层 | 中 | **P1** | `photo.py:43`（`AUTO_PASS=0.9`） | 否 |
| 7 | Excel 路径标注无降本，教师负担=题数 | 采集层 | 中 | **P1** | `schemas.py:35`、`excel.py` | 否 |
| 8 | 归因沿未验证图谱遍历，缺边即盲区且无发现机制 | 归因层 | 中 | **P1** | `graph.py:73`、`attribution.py:94` | 否（但隐性） |
| 9 | 诊断题证伪闭环未建，归因"可证伪"是纸面承诺 | 归因/干预 | 高 | **P1** | DESIGN §7 未实现 | 是（无法自校正） |
| 10 | 题库飞轮未建（`bank_question` 空表），干预层空心 | 干预层 | 中 | **P1** | DESIGN §8 未实现 | 否（但干预无内容） |
| 11 | 掌握度加权平均简陋（vs IRT/BKT） | 追踪层 | 低 | **P2** | `mastery.py:45-57` | 否（MVP 取舍） |
| 12 | 猜测校正题级而非学生级 | 追踪层 | 低 | **P2** | `evidence.py:71-74` | 否 |
| 13 | 半衰期固定，不区分知识点类型 | 追踪层 | 低 | **P2** | `config.py:31` | 否 |
| 14 | 班级 P25 冷启动（需 ≥4 有效学生） | 追踪层 | 低 | **P2** | `weakness.py:135` | 否 |
| 15 | 根源选择启发式（最低 + 最深）可疑 | 归因层 | 低 | **P2** | `attribution.py:116` | 否 |
| 16 | `grade7_kp_ids` 用 `max(grades)` 取主年级，脆弱假设 | 知识库层 | 低 | **P2** | `graph.py:95` | 否 |
| 17 | networkx 视图建了未用 | 知识库层 | 极低 | **P3** | `graph.py:45` | 否 |

---

## 1. 缺陷一：失分归属不可解（P0）

### 1.1 现状（代码实证）

`evidence.py:81-97`，一条作答派生证据：

```python
for qkp in question.kps:                       # 一道题标注的每个 kp
    weight = source_weight * qkp.weight * cascade * anomaly_factor
    session.add(EvidenceEvent(
        ...
        value=round(value, 6),                 # ← 同一个 value 写给所有 kp
        weight=round(weight, 6),
    ))
```

其中 `value` 在 `evidence.py:70-76` 由题目整体得分率算出（选择题猜测校正）：

```python
rate = max(0.0, min(1.0, answer.score / question.full_score))
if question.q_type == "选择":
    value = max(0.0, (rate - g) / (1.0 - g))
else:
    value = rate
```

`qkp.weight`（题内知识点分摊权重，`prompts.py:28` 要求同题权重和=1）只进了 `EvidenceEvent.weight`（掌握度加权分母），**不进 `value`**。

### 1.2 缺陷

一道考了 kp A（权重 0.6）与 kp B（权重 0.4）的综合题，学生得 60%：**A 和 B 拿到完全相同的 `value=0.6`**。但 `value` 才是掌握度分子。这意味着：

- 用一个标量得分率，同时代表多个 kp 的独立掌握度——**信息量不够，物理上不可解**；
- 学生可能 A 完全会、B 完全不会，系统却记录 A 也只有 0.6 → **系统性高估薄弱点、污染所有相关 kp**；
- 一道综合题全错，所有标注 kp 同时变弱 → 归因去这些 kp 里找"最低前置"当根源，但"最低"可能就是被这道错题污染出来的 → **归因在追自己制造的噪声**。

DESIGN §6 写"基础值=得分/满分，按题内知识点权重 w 分摊"，但实现里 weight 只进加权，**value 没按 kp 分摊**（也无法分摊）。这是设计与实现的偏差，更是设计本身的极限。

### 1.3 影响（含金标假象）

归因精度的天花板被此锁死。金标根源命中 **0.96**（`simulator/test_gold.py`）是在合成数据上测得——合成数据给每个 kp 植入了**独立的真值掌握度**，恰好绕开了失分归属噪声。真实试卷噪声远大于此，DESIGN 自己估计"端到端六七成"。**0.96 不可外推到真实场景**，当前没有任何机制补偿这一损失。

### 1.4 改进方案

| 方案 | 做法 | 成本 | 效果 | 评价 |
|---|---|---|---|---|
| A. 诊断题探针 | 为疑似薄弱 kp 出**独立**诊断题（只考该 kp），直接探掌握度 | 高（需题库+流程） | 根治 | DESIGN §7 本就规划，提前到 P0 |
| B. 选项迷思定位 | 选择题记录所选选项（已采），用干扰项频次定位具体迷思 kp | 中 | 部分（仅选择题） | DESIGN §7 标 P1，可提前 |
| C. 修正建模语义 | 把 `qkp.weight` 真正用于分配失分（如失分按 weight 摊给各 kp 的"负证据"） | 低 | 治标不治本（weight 本身不准） | 短期止血 |
| D. 简化 IRT | 为每个 (题, kp) 估计区分度参数，用历史数据拟合 | 很高 | 好 | 超出 MVP，归入 P2 |

**推荐：C（短期）+ A/B（中期）。**

- **短期（C）**：在 `evidence.py` 增加"失分归属"语义——对做错的题，按 `qkp.weight` 把"失分证据"分配给各 kp，而非把整体得分率无差别写入。至少让 weight 不再是纯装饰，并让"全错题"不再等量污染所有 kp。**注意**：这只是缓解，因为 weight 由 LLM/教师给出，本身不准——所以必须配合方案 A/B 才能根治。
- **中期（A/B）**：把诊断题（A）与迷思概念（B）从 DESIGN 的 P1 提前。诊断题是失分归属的**根本解法**：它绕开"从综合题反推"，直接测单 kp。这也让归因的"可证伪"承诺（缺陷 9）得以兑现。

### 1.5 涉及代码变更

- `evidence.py:derive_events_for_response`：增加按 weight 的失分分配逻辑（方案 C）；
- 新增 `pipeline/diagnostic.py` + 路由：诊断题作答 -> 单 kp 证据（方案 A）；
- `attribution.py`：归因生成诊断题推荐（呼应 `prediction` 字段，当前只是文字）。

---

## 2. 缺陷二：知识库地基未审 + 建模失效（P0/P1）

### 2.1 draft 草图兜底当 active（P0）

**现状**：`kb.yaml` 顶部明确 `status: draft`、"未经教研审核"。`routes.py:93-106 _active_kb`：

```python
kb = session.scalar(select(KbVersion).where(KbVersion.status == "active").order_by(...))
if kb is None:
    kb = session.scalar(select(KbVersion).order_by(KbVersion.id.desc()))  # 兜底取最新
```

[kb-edit-design.md](kb-edit-design.md) §3.2 已把"不看 status"改成"优先 active"，但**兜底仍在**：只要没人手动切 active，整个分析层就跑在 LLM 起草、未经审核的图谱上。归因的"根源"完全依赖 prerequisite 边的正确性，而这些边是 LLM 拍的。

**改进**：收紧 `_active_kb`——无 active 时**报错**（`400 尚无审核通过的知识库`），不再兜底。冷启动流程显式引导教研审核（或在 Wizard 里加"我确认草稿可用"的显式激活动作，写 `CorrectionLog` 留痕）。保留兜底仅限 `SC_KB_ALLOW_DRAFT_FALLBACK` 环境变量开启的演示场景。

### 2.2 图谱 prerequisite 边零校验（P0）

**现状**：DESIGN §4.4 设计了"数据反查图谱：若某前置边两端掌握度长期无相关性，标记可疑边进入复核队列"，但**代码未实现**。当前对边的正确性零校验，归因完全信任 LLM 草图。

**改进**：新增 `scripts/audit_kb_edges.py`（或 `kb/graph.py` 加 `suspect_edges()`）：
- 对每条 prerequisite 边 (from→to)，取班级层面两端 kp 的掌握度序列，计算相关性；
- 相关性低于阈值（如 ρ < 0.3）且样本够（两端各有 ≥N 学生有证据）→ 标记可疑，进复核队列；
- 结果挂到 `KpRelation.audit_status`（字段已存在于数据模型 §10）或新建 `kb_edge_audit` 表。

这让"图谱缺边/错边"从隐性变为可观测，是缺陷 8（归因盲区）的发现机制。

### 2.3 prerequisite `weight` 是死字段（P1）

**现状**：`kb.yaml` 每条前置边标了 weight（0.5~0.9），`graph.py:73 prerequisite_chain` 返回了 weight，但归因 `attribution.py:104` 直接丢弃：

```python
for anc_id, depth, _edge_w in graph.prerequisite_chain(...):   # ← weight 丢弃
    ...
    if m is not None and m < PREREQ_ROOT_THRESHOLD:            # 只看掌握度
        low_ancestors.append(...)
low_ancestors.sort(key=lambda t: (t[2], -t[1]))                # 按 mastery 排序，仍不看 weight
```

LLM 拍的 0.5 vs 0.9 对归因结果**毫无影响**。"建了模型没用"是最差中间态：既增加 LLM 标注负担和出错面，又没换来任何精度。

**改进（二选一，推荐前者）**：
- **让 weight 参与归因**：根源选择改为"前置强度加权"——`score = (floor - mastery) * edge_weight`，强前置（0.9）的同等掌握度差更可能是根源；弱前置（0.5）降权。同时在 2.2 的可疑边反查里用 weight 做先验。
- **删除 weight**：若评审认为 LLM 拍的 weight 不可信，则删字段、简化标注 prompt（`prompts.py:28` 去掉 weight 要求），减少出错面。

### 2.4 其他知识库建模问题（P2）

- **根源选择启发式**（`attribution.py:116`）：取掌握度最低的前置点，并列取更深的。"更深"≠"更根本"，可能只是更远的弱 kp。DESIGN 决策记录已承认深度优先会被噪声误导，改取最低，但这只是换了个问题。建议结合 2.3 的 weight 加权后重审。
- **`grade7_kp_ids` 用 `max(grades)` 取主年级**（`graph.py:95`）：当前 M6+M7 混合，max=7 正确；但若图谱混入更高年级节点（多教材/跨年级扩展）即出错。建议改为按 `kb_version` 的 `meta.grade` 显式指定主年级，而非推导。

---

## 3. 缺陷三：题-kp 标注咽喉无保障（P0/P1）

精度上限完全由"题-kp 标注质量"决定，但这层最薄弱。

### 3.1 标注质量不可观测（P0）

**现状**：无法知道 LLM/教师标得对不对。DESIGN 规划的"人工标注 200 题（标注金标）"是 v0 计划，当前只有合成金标。真实标注误差**不可观测**——系统精度是黑盒。

**改进**：建立真实人工金标（200 题，覆盖各题型/各章），作为标注质量的回归基线。每次换模型/改 prompt 必跑，输出"标注字段级准确率"。**这是验明系统生死的唯一手段**，优先级 P0。配套：标注一致性指标（同一题多次标注的吻合率），监控 LLM 标注稳定性。

### 3.2 批量批准可绕过逐题复核（P1）

**现状复核（已纠正初版判断）**：阶段 A 的题-kp 标注并不按 `AUTO_PASS=0.9` 自动审核；所有 `QuestionKp` 初始均为 `reviewed_at=None`，都会进入待审队列。`AUTO_PASS` 原本只控制阶段 B 的得分抽取审核。真实风险在于审核台的“批准全部待审标注”会一次性给所有标注落闸，教师可能未逐题查看，导致高置信系统性错误仍可被批量放过。

**改进（✅ 已落地）**：新增 `SC_TAG_REVIEW_SAMPLE_RATE`（默认 0 保持历史工作流，试点/生产建议 0.1）。开启时：
- 低置信标注（confidence <0.9）永不批量通过；
- 高置信题按 `(template_id, question_id)` 的 SHA-256 稳定哈希抽样，抽中题保留待审；同题多个 kp 标签始终一起抽中；
- 审核队列返回 `review_reason=低置信标注|高置信抽样|待批量批准`，前端明确展示原因；
- `approve-tags` 响应新增 `pending` 题数，提醒教师逐题保存后才真正落闸。

落地点：`config.py:TAG_REVIEW_SAMPLE_RATE`、`photo.py:_tag_sampled/approve_template_tags/review_queue`、`Review.tsx`。聚焦验证：后端 18 passed + 前端 build 成功。后续仍需加入“标注一致性”提示：某 kp 近期被高频修改时提示定义可能模糊（呼应 DESIGN §4.5）。

### 3.3 Excel 路径标注无降本（P1）

**现状**：Excel 是 P0 入口，但知识点关联发生在教师建模板时手填 `kps[{code, weight}]`（`schemas.py:35 QuestionCreate`）。普通教师能否准确标注每题的知识点+权重存疑（这其实是教研专业工作）。且 DESIGN 说两阶段解析把标注量从"学生数×题数"降为"题数"——但 Excel 路径本来就是"题数"级，**没有任何降本**。

**改进**：
- Excel 路径也接入 LLM 闭集标注（用题干文本，不必拍照），教师只审核不手填；
- 或提供"题干 -> 推荐 kp"的辅助接口，降低教师手填门槛；
- 标注来源（`QuestionKp.source`）区分 LLM/教师，便于飞轮分析。

---

## 4. 缺陷四：归因检索盲区（P1）

### 4.1 缺边即盲区

**现状**：`prerequisite_chain`（`graph.py:73`）只能找**已建模**的前置边。kb.yaml 的边是 LLM 起草，必然缺边。若真实根源 kp 没连到当前薄弱 kp 的前置链上，归因直接找不到——返回"数据不足"或指向错误根源。

### 4.2 无缺边发现机制

**改进**：与 2.2 联动。可疑边反查能发现"错边"，但发现"缺边"更难。建议：
- 归因失败（找到的根源经诊断题证伪，或多个薄弱点无公共前置祖先）时，记录"疑似缺边"候选（薄弱 kp 与班级低掌握 kp 之间的潜在前置），进复核队列；
- 这是 DESIGN §7 "共性因素"假设（P2）的弱化版前置：当个人弱且班级也弱、又找不到前置根源时，标记"疑似图谱缺边/横切因素"。

---

## 5. 缺陷五：掌握度模型简陋（P2，MVP 取舍确认）

DESIGN 明确把 IRT/DKT 列为不做，这层是**可接受的 MVP 取舍**，但需显式记录其与缺陷 1 叠加会放大误差。

| 子项 | 现状 | 影响 | 改进（P2） |
|---|---|---|---|
| 加权平均 vs IRT/BKT | `mastery.py:45-57` 时间衰减加权平均 | 无法区分"猜对"与"会" | 数据量到位后引入 BKT（学生级猜测概率） |
| 猜测校正题级 | `evidence.py:71-74`，g=1/选项数 | 不区分学生猜测倾向 | 配合 BKT 做学生级校正 |
| 半衰期固定 | `config.py:31` 考试60/练习30 | "识记"遗忘快、"应用"慢，一刀切 | 按 `cog_levels_expected` 分档半衰期 |
| 班级 P25 冷启动 | `weakness.py:135` 需 ≥4 有效学生 | 小班/冷启动班级参照失效 | 无班级数据时回退"教师评定难度档"（DESIGN §6 已提及但未实现） |

**关键提醒**：缺陷 1（失分归属）不解决，单纯升级掌握度模型（11/12）收益有限——输入信号失真，模型再精也放大噪声。**故 11/12 排在 1 之后。**

---

## 6. 其他：规划未到位项（P1，非缺陷而是未实现）

DESIGN 已设计但 MVP 未实现，与上述缺陷强相关，建议提前：

- **诊断题证伪闭环**（DESIGN §7）：归因 `prediction` 字段当前只是文字（`attribution.py:134`），无实际证伪流程。这是缺陷 1 的根治手段 + 归因自校正的前提。**建议提到 P1。**
- **题库飞轮**（DESIGN §8）：`bank_question` 表已建但空，干预层无内容来源。审核入库的题目应自动进题库，干预内容 = 错题重做/变式 + 同 kp 同难度未做过题。
- **networkx 视图未用**（`graph.py:45`）：建了 `self.nx_graph` 但核心遍历用 dict。要么用于可视化/可疑边反查（2.2 可复用），要么删除减少困惑。

---

## 7. 落地路线图

### 阶段一（P0，止血——让精度可观测、地基可信任）

1. **收紧 `_active_kb`**（2.1）：draft 不兜底，无 active 报错。~0.5 天
2. **修正失分归属建模语义**（1.4-C）：`evidence.py` 按 weight 分配失分。~1 天
3. **建立真实人工金标**（3.1）：200 题标注金标 + 字段级准确率回归。~3-5 天（含人工标注）
4. **图谱可疑边反查**（2.2）：`audit_kb_edges`，让错边可观测。~1-2 天

> 阶段一目标：系统精度从"黑盒"变"可观测"，归因不再跑在未审图谱上，失分归属有缓解。

### 阶段二（P1，补强——闭环与标注质量）

5. **诊断题证伪闭环**（6 + 1.4-A）：诊断题路由 + 单 kp 证据 + 归因证伪。~3-5 天
6. **让 weight 参与归因**（2.3）：根源选择按前置强度加权。~0.5 天
7. **高置信抽样复核**（3.2）：审核台抽样 + 一致性提示。~1 天
8. **Excel 路径 LLM 辅助标注**（3.3）：题干->推荐 kp。~2 天
9. **题库飞轮**（6）：审核入库自动进题库。~2 天

### 阶段三（P2，演进——模型升级，等数据到位）

10. 掌握度 BKT / 学生级猜测校正（5）
11. 按 cog_level 分档半衰期（5）
12. 班级参照冷启动回退（5）
13. 根源选择重审（2.4）、`grade7_kp_ids` 显式主年级（2.4）

---

## 8. 不改动项（显式声明，避免反复讨论）

以下为 DESIGN 明确的 MVP 边界，**本次不动**，仅记录：

- IRT / DKT / 深度模型（数据量不够，P2 后再议）；
- 多学科 / 学生家长端 / 账号权限（超出 MVP）；
- 排名类功能（合规禁止，永不做）；
- 四条架构不变量（只读已提交数据 / derive-on-read / LLM 必经闸门 / 报告零幻觉）——本改进严格遵守，所有变更不得突破。

---

## 附：关键参数当前值（`config.py`，改进时按需调整）

| 参数 | 当前值 | 相关缺陷 |
|---|---|---|
| `MIN_EVIDENCE_COUNT` | 3 | 证据门槛 |
| `PREREQ_MAX_DEPTH` | 3 | 归因下探深度 |
| `PREREQ_ROOT_THRESHOLD` | 0.6 | 前置点判低阈值 |
| `DEFAULT_MASTERY_FLOOR` | 0.6 | 绝对底线 |
| `CLASS_PERCENTILE` | 25 | 班级 P25 |
| `CLASS_COMMON_WEAK_RATIO` | 0.40 | 班级共性阈值 |
| `STALE_DAYS` | 90 | 证据过期 |
| `HALF_LIFE_DAYS` | 考试60 / 练习诊断30 | 缺陷 13 |
| `SOURCE_TYPE_WEIGHT` | 期中/期末/补录1.0 · 单元0.8 · 诊断0.7 · 练习0.5 | 证据权重 |
| `AUTO_PASS`（`photo.py`） | 0.9 | 缺陷 6 |
