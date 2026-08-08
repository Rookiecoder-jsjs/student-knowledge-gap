# 知识图谱改进 - 设计文档 v0.1

> 状态：第一、二批已落地（2026-08-08）· 所在目录：项目根（与 `improvement-plan.md` / `effectiveness-validation-plan.md` 同级）
>
> 范围：针对 `kb/math/grade7/kb.yaml` 知识图谱的**结构质量、属性消费、归因支撑**做改进设计。本文不改 DESIGN 的能力边界与四条不变量，只回答一个问题：**这张图够不够好、哪些地方是死结构、怎么让它在效率与质量间取得平衡。**
>
> 与已有文档的关系：`improvement-plan.md` 聚焦分析正确性的建模改进（失分归属、归因闭环）；`effectiveness-validation-plan.md` 聚焦诊断有效性的证据与可信度；本文聚焦**知识图谱本身**--节点/边/属性的合理性与消费率。三者互补不重叠。
>
> 触发依据：对 `kb/math/grade7/kb.yaml`、`app/kb/{loader,graph}.py`、`app/pipeline/{mastery,weakness,attribution}.py`、`app/models.py` 的代码审查 + 图谱拓扑分析（40 节点 56 边）+ 大规模随机模拟（150 人 × 12 场 × 6 种子）的归因有效性结果。

---

## 0. 问题总览（图谱改进矩阵）

| # | 问题 | 主题 | 严重度 | 证据 | 代码可执行？ |
|---|---|---|---|---|---|
| K1 | confusable/spiral 关系零消费 | 结构消费 | **高** | `graph.py:41` 只加载 prerequisite；confusable 3 边 / spiral 2 边存储后无任何分析读取 | 是（激活归因路径） |
| K2 | mastery_floor 稀疏，综合题与识记题同底线 | 判定质量 | **高** | 40 个 grade7 KP 中仅 4 个标 0.7，其余 36 个默认 0.6；建模类综合题与正负数概念同底线 | 是（按 cog 派生） |
| K3 | 边权全为 LLM 估计，无数据校验 | 归因质量 | **高** | `kb.yaml` 边权 0.5~0.9 均匀分布；`suspect_edges`(`graph.py:106`) 只报警不自愈 | 部分（批处理+人工确认） |
| K4 | 仅后向归因，无前向影响预警 | 诊断丰富度 | 中 | `prerequisite_chain`(`graph.py:75`) 只向后找祖先，无 descendants 接口；报告无"波及下游"提示 | 是（加 graph 方法） |
| K5 | 节点无重要度，报告无优先级 | 报告质量 | 中 | `KnowledgePoint`(`models.py:98`) 无 importance 字段；薄弱清单平铺无排序 | 是（加字段+排序） |
| K6 | 粒度不均，细章节证据饥饿 | 覆盖质量 | 中 | 第一章 16 KP / 第二章 6 KP；细 KP 单元测常 < MIN_EVIDENCE_COUNT 判数据不足 | 部分（软聚类兜底） |
| K7 | difficulty_prior / cog_level 名不副实 | 结构清理 | 低 | `routes.py:1058` 自注"difficulty_prior 未参与掌握度"；`cog_level` 记录但主路径不过滤(`weakness.py:152`) | 是（激活或改名） |

> 核心判断：**图谱结构基本健康（4 根 11 叶、跨年级桥接 8/8 全覆盖），问题在"结构够、消费不够"**。confusable/spiral/cog_level/floor 都是已有语义却零消费或低消费的死字段。优先级应是**激活存量**（K1/K2/K4/K7）而非加新节点新边--不增维护负担，质量直接涨。K3（数据驱动边权）是唯一需要新机制的，但也是质量天花板最高的一项。

---

## 1. K1 · confusable / spiral 关系零消费

### 现状（代码实证）

图谱声明 4 种关系，`loader.py:19` 与 `routes.py:949` 都校验 `{"prerequisite","contains","confusable","spiral"}`，但 `KpGraph.__init__` 只加载一种：

```python
# graph.py:40-44 -- 只取 prerequisite，confusable/spiral 进了库再无读取
for rel in session.scalars(
    select(KpRelation).where(KpRelation.type == "prerequisite")
):
    ...self._prereq[rel.to_kp_id].append((rel.from_kp_id, rel.weight))
```

`kb.yaml` 里 `confusable` 3 条（104↔105 相反数/绝对值、201↔202 单项式/多项式、121↔114 乘方/乘法符号）、`spiral` 2 条（M6-01→122 四则律→混合运算、M6-04→304 简易方程→方程解法）--全是教研有价值的语义，存了不用。

归因层 `attribute_assessment`(`attribution.py:63`) 只产三类假设：前置缺陷 / 遗忘衰减 / 数据不足。**"概念混淆"这个高频数学错因完全没有归因维度**。学生把"相反数"和"倒数"搞混，系统只会判"前置缺陷"或"遗忘"，指向错误。

### 方案

**A. 激活 confusable 归因（代码 now）** -- `KpGraph` 加载 confusable 边为 `_confusable: dict[int, list[int]]`（双向）。归因层加第四类假设 `ATTR_CONFUSABLE = "易混淆"`：

```python
# attribution.py -- attribute_assessment 内，is_weak 且非 low_evidence 时
def _confusable_pair(session, graph, student_id, a, as_of):
    partners = graph.confusable_partners(a.kp_id)
    weak_partners = []
    for pid in partners:
        if evidence_summary(session, student_id, pid, as_of).count < EVIDENCE_LOW_WATERMARK:
            continue
        m = mastery_at(session, student_id, pid, as_of)
        if m is not None and m < PREREQ_ROOT_THRESHOLD:  # 伙伴也弱
            weak_partners.append((pid, m))
    if not weak_partners:
        return []
    return [AttributionFinding(
        kp_id=a.kp_id, type=ATTR_CONFUSABLE, confidence=0.65,
        evidence=[{"confused_with": graph.kp(p).code, "mastery": round(m,3)} for p,m in weak_partners],
        prediction=f"如果是混淆了「{a.kp_name}」与「{graph.kp(weak_partners[0][0]).name}」，"
                   f"做区分两者的诊断题该生也会错。可用 2~3 道对比题验证。",
    )]
```

`run_attribution_for_student` 的 findings 收集补 `findings.extend(_confusable_pair(...))`。归因 upsert 的 key 已是 `(kp_id, type)`，新类型天然不冲突。

**B. 补充 confusable 边（人工 later）** -- 3 条太少。数学七上至少补到 10-15 条：相反数↔倒数(104↔115)、绝对值↔相反数(105↔104)、等式性质↔方程的解(302↔301)、去括号符号↔合并同类项(204↔203) 等。LLM 可起草初版，教师确认。

### 验收
- `KpGraph.confusable_partners(kp_id)` 返回双向伙伴列表。
- 归因新增 `易混淆` 类型；当薄弱 KP 的 confusable 伙伴也弱时产出假设，prediction 含区分诊断题建议。
- 单测：植入"混淆对"（两 KP 同弱但前置链正常），断言产出 ATTR_CONFUSABLE 而非误判前置缺陷。
- spiral 关系暂不激活（见 K7），但留 schema。

### 执行性
- **代码 now**：graph 加载 + 归因函数 + 单测。照搬 prereq 的加载与归因模式，改动集中。
- **人工 later**：补 10-15 条 confusable 边（教研确认）。

---

## 2. K2 · mastery_floor 稀疏，综合题与识记题同底线

### 现状（代码实证）

`weakness.py:127` 取 `floor=kp.mastery_floor`，薄弱判据是 `mastery < floor`（`:183-193`）。但 `kb.yaml` 里：

```yaml
# 仅 4 个 grade7 KP 标了 0.7：M7A-105(绝对值) / M7A-111(加法) / M7A-114(乘法) / M7A-303(方程解法)
# 其余 36 个全是 models.py:113 默认 0.6
```

问题在于**底线没反映认知负荷**。大规模随机模拟的残余误报 ~0.21（`effectiveness-largescale-results.md`），主因即边界学生（基础 0.65-0.70）在有限题量下估计值跌破 0.6。但同一 0.6 底线用在两类完全不同的 KP 上：

- **综合级**（M7A-305 建模、M7A-306 行程问题，`cog_levels_expected: [综合]`）：难度 0.65，全班常态掌握 0.5-0.6。用 0.6 底线 -> 大量学生"假正常"（其实综合题 0.6 已经不错了，不该再判薄弱，但反过来说低于 0.6 也未必真薄弱）。
- **识记级**（M7A-101 正负数概念，`cog_levels_expected: [理解]`）：难度 0.30，常态掌握 0.85+。用 0.6 底线 -> 0.65 的学生被判"正常"，但识记级 0.65 实际是明显薄弱。

底线一刀切，既放过综合题的真薄弱，又放过识记题的假正常。

### 方案

**按认知层级派生默认 floor（代码 now）** -- `cog_levels_expected` 每个 KP 都有，直接派生：

```python
# config.py -- 新增认知层级底线映射（教师可逐 KP 覆盖 mastery_floor）
COG_FLOOR_DEFAULTS = {
    "识记": 0.70,   # 基础记忆，底线高
    "理解": 0.65,
    "应用": 0.60,
    "综合": 0.55,   # 高阶综合，底线低（0.55 已是较好水平）
}
# loader.py / weakness.py -- floor 取 max(kp.mastery_floor, cog_floor_default)
#   即：KP 显式标了 floor 用显式值；否则按 cog 层级派生。
#   显式标注仍优先（M7A-105 等地基点保持 0.70）。
```

落地方式：`weakness.py:127` 改为 `floor = kp.mastery_floor if kp.mastery_floor != DEFAULT_MASTERY_FLOOR else COG_FLOOR_DEFAULTS.get(主导cog, 0.6)`。或更干净：loader 导入时把未显式标注的 KP 按 cog 派生 floor 写入 `mastery_floor` 字段（一次性，前端可见可改）。

**取舍**：派生值是经验基线，最好教研确认。但即使先按经验上，也比"全 0.6"合理。`?preview=true` 已支持 floor 改动预览薄弱人数变化（`routes.py:1031`），教师可在前端调。

### 验收
- 未显式标 floor 的 KP，按 `cog_levels_expected` 主导层级派生底线。
- 综合级 KP 底线 ≤ 0.55，识记级 ≥ 0.70。
- 大规模模拟重跑：识记级假正常减少、综合级假薄弱减少，FP 期望从 ~0.21 下降（量化验收）。
- 金标召回不退化（植入薄弱 0.25-0.50 远低于任何底线）。

### 执行性
- **代码 now**：config 常量 + loader/weakness 派生逻辑 + 模拟重跑验收。
- **人工 later**：教研复核各层级底线值（识记 0.70 / 应用 0.60 / 综合 0.55 是否合理）。

---

## 3. K3 · 边权全为 LLM 估计，无数据校验

### 现状（代码实证）

`kb.yaml` 56 条 prereq 边，权重 0.5~0.9 均匀分布，全是 LLM 起草（头注 v0.1.0 待审核）。归因选根源用 `root_score = gap × (0.5 + 0.5·w)`（`attribution.py:123-126`），权重直接影响"挑哪个祖先当真凶"。

`suspect_edges`(`graph.py:106-168`) 已经能算每条边两端的掌握度相关性：

```python
# graph.py:152 -- 算好了 corr，但只用于"可疑边"报警，不回写权重
corr = _pearson(xs, ys)
if abs(corr) < corr_threshold:  # < 0.3 可疑
    suspects.append({...})
```

即：**数据已经算出来了，但只用来报警，不用来自愈**。大规模模拟 root-hit 0.863 的 14% miss，一部分来自噪声祖先被高权边错误选中。

### 方案

**数据驱动的边权精炼（代码 now 批处理 + 人工确认）** -- 新增 `scripts/refine_edge_weights.py`，定期批处理：

```python
# 对每条 prereq 边 (from->to)：
#   1. 取两端均达 MIN_EVIDENCE_COUNT 的学生样本（复用 suspect_edges 逻辑）
#   2. 算 observed_corr = pearson(mastery_from, mastery_to)
#   3. 贝叶斯收缩：weight_posterior = α·weight_prior + (1-α)·|observed_corr|
#      α = n / (n + K)  -- 样本越多越信数据，K=先验强度（如 10）
#   4. 若 |observed_corr| < 0.2 且 n >= 8：标 audit_status="待复核"，建议降权到 0.3
#   5. 不自动改图，只产 diff 报告 + 建议权重，教师在前端 /kb 确认后落库
```

**关键约束**：
- **不自动改图**--只产建议，人工确认（避免误伤真边，违背"教师否决权"纪律）。
- 样本不足（n < 8）不触发--前期数据少时保持 LLM 先验，避免噪声驱动。
- 与 K7 配合：`difficulty_prior` 也可走同样的贝叶斯框架（先验 + 数据后验）。

### 验收
- 脚本对每条边输出 `{edge, prior_weight, observed_corr, n, suggested_weight, action}`。
- 高相关边（corr > 0.5）权重维持或微调；低相关边（corr < 0.2, n >= 8）标"待复核"。
- 教师确认后落库；`audit_status` 从 draft -> reviewed。
- 大规模模拟重跑（用精炼后权重）：root-hit 期望从 0.863 提升（量化验收）。

### 执行性
- **代码 now**：批处理脚本 + diff 报告。复用 suspect_edges 的相关性计算。
- **人工 later**：教师逐条确认建议权重（前端 /kb 审核界面）。需真实考试数据（每边两端 ≥8 样本）才触发。

---

## 4. K4 · 仅后向归因，无前向影响预警

### 现状（代码实证）

`KpGraph` 只有向后找祖先的 `prerequisite_chain`(`graph.py:75`)，没有向前找后代的接口。归因 `attribute_assessment` 也只问"X 薄弱是因为哪个祖先弱"，不问"X 薄弱会波及哪些后代"。

大规模模拟里我临时建了 `descendant_map`（`simulator/large_scale.py` 的 `_descendant_map`）--逻辑现成，但只是测试脚本的局部工具，没进图谱正式能力。

教师拿到诊断单，看到"该生 M7A-105 绝对值薄弱"，但看不到"这会拖累 106/111/112/113/114"的连锁风险。干预缺乏"先补地基"的前瞻抓手。

### 方案

**前向影响视图（代码 now）** -- `KpGraph` 加正式方法：

```python
# graph.py -- 前向 BFS 找后代（depth ≤ PREREQ_MAX_DEPTH）
def descendants(self, kp_id: int, max_depth: int = PREREQ_MAX_DEPTH) -> list[tuple[int, int, float]]:
    """前向影响：kp_id 薄弱会波及的后代 [(descendant_id, depth, edge_weight), ...]。
    depth 从 1 开始；同一后代只保留最浅路径。"""
    seen: dict[int, tuple[int, float]] = {}
    frontier = [(kp_id, 0)]
    while frontier:
        cur, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for to_id, pres in self._prereq.items():  # 找以 cur 为前置的 to
            for pre_id, w in pres:
                if pre_id == cur and to_id not in seen:
                    seen[to_id] = (depth + 1, w)
                    frontier.append((to_id, depth + 1))
    return [(did, d, w) for did, (d, w) in sorted(seen.items(), key=lambda kv: kv[1][0])]
```

报告层（`reports/student_diagnosis.py`）在薄弱 KP 后追加一句：

```
⚠ 影响预警：M7A-105 绝对值薄弱，可能波及 M7A-106(大小比较)、M7A-111(加法)、...
  建议优先补强该地基点，可一并缓解下游。
```

**纪律约束**：前向是预测，置信度低于后向归因。报告必须标"可能波及/风险"而非"结论"，且仅对 depth=1 的直接后代高置信，depth≥2 标"间接"。

### 验收
- `KpGraph.descendants(kp_id)` 返回后代列表，与 `_descendant_map` 结果一致。
- 诊断单对每个薄弱 KP 追加前向影响预警（depth≤2）。
- 预警措辞为"可能波及"，非结论性。
- 单测：M7A-105 的后代含 106/111/112/113/114（depth 1-2）。

### 执行性
- **代码 now**：graph 方法 + 报告渲染。BFS 可缓存，零额外查询成本。
- **人工 later**：无（纯代码能力）。

---

## 5. K5 · 节点无重要度，报告无优先级

### 现状（代码实证）

`KnowledgePoint`(`models.py:98-114`) 字段：code/name/grade/semester/chapter/cog_levels_expected/difficulty_prior/mastery_floor/archived。**没有重要度/优先级字段**。

报告层（质量分析、个人诊断单）的薄弱清单平铺列出，无排序。但"绝对值"(M7A-105，有理数运算地基)和"科学记数法"(M7A-123，相对独立技能)显然不在一个量级--教师应先补地基。全局薄弱判定(`GLOBAL_WEAK_RATIO`)也把所有 KP 等权计入，一个"拓展级"薄弱和一个"基础级"薄弱同等拉高 weak_frac。

### 方案

**节点重要度（代码 now + 人工标注）** -- `KnowledgePoint` 加 `importance` 字段：

```python
# models.py -- 新增
importance: Mapped[str] = mapped_column(String(10), default="核心")  # 基础 / 核心 / 拓展
# loader.py -- 从 YAML 读 importance（缺省"核心"）
# kb.yaml -- 每个 KP 加 importance 标注
```

消费点：
- **报告排序**：薄弱清单按 `基础 > 核心 > 拓展` 排，同级别按掌握度缺口降序。
- **全局薄弱加权**：`GLOBAL_WEAK_RATIO` 计算时，基础级薄弱权重 ×1.5，拓展级 ×0.5--避免"拓展题做不好"被当成"全局基础差"。
- **干预建议**：诊断单优先推荐基础级薄弱的诊断题。

**取舍**：重要度是教学判断，需教师标一轮。但标注成本低（3 档），且 `?preview=true` 已支持属性改动预览。

### 验收
- KP 支持 importance 字段；前端 /kb 可编辑。
- 报告薄弱清单按重要度排序。
- 全局薄弱判定对基础/拓展开权区分。
- 单测：基础级 + 拓展级同弱时，基础级排序在前、诊断题优先推荐。

### 执行性
- **代码 now**：字段 + loader + 报告排序 + 加权逻辑。
- **人工 later**：教师标注 40 个 KP 的 importance（3 档，半小时）。

---

## 6. K6 · 粒度不均，细章节证据饥饿

### 现状（代码实证）

第一章 16 个 KP（有理数，拆得很细：概念/数轴/相反数/绝对值/加减乘除/乘方/混合/科学记数法/近似数），第二章 6 个。`MIN_EVIDENCE_COUNT=2`（生产默认）下，单元测覆盖第一章时 16 个 KP 分摊题量，每个 KP 单场考试常只有 1 题--两场才达门槛，期中前第一章多数 KP 判"数据不足"。

大规模模拟轨迹（`effectiveness-largescale-results.md`）证实：覆盖率在期中(上)才跃升到 0.55，前期单元测阶段第一章 16 个细 KP 普遍饥饿。

### 方案

**技能簇软聚类（代码 now 兜底 + 人工定义簇）** -- 不拆/不并 KP（破坏存量数据），加"技能簇"概念：

```yaml
# kb.yaml -- 新增 clusters 段（可选）
clusters:
  - {code: CL7A-1A, name: 有理数概念与表示, members: [M7A-101, M7A-102, M7A-103, M7A-104]}
  - {code: CL7A-1B, name: 有理数运算, members: [M7A-111, M7A-112, M7A-113, M7A-114, M7A-115, M7A-116]}
```

```python
# weakness.py -- 单 KP 证据不足时，回退到簇评估
if len(events) < MIN_EVIDENCE_COUNT and kp.cluster_id is not None:
    cluster_events = [e for member in cluster_members for e in events_for(sid, member)]
    if len(cluster_events) >= MIN_EVIDENCE_COUNT:
        a.mastery = mastery_of_events(cluster_events, as_of)
        a.cluster_assessed = True  # 标记：簇级评估，精度较低
        # 仍标 low_evidence 提示精度限制
```

报告措辞："该生在'有理数运算'簇上薄弱（簇级评估，定位精度较低，建议补测细化）"。

**取舍**：簇掌握度不如单点精确，**只在单点饥饿时兜底**，不替代单点评估。簇定义要教研给。实现中等（簇->成员映射 + 聚合 + 报告标注）。优先级低于 K1/K2/K4--先做激活存量的，饥饿问题在生产配置(MIN=2)+ 累积型大考下已大幅缓解（覆盖率期末达 1.0）。

### 验收
- 单 KP 饥饿时回退簇评估，`cluster_assessed=True` 标记。
- 簇级评估 mastery 由成员合计证据推导。
- 报告明确标注"簇级评估，精度较低"。
- 单测：4 个 KP 各 1 证据（单点饥饿），簇合计 4 证据 -> 簇级可评估。

### 执行性
- **代码 now**：簇加载 + 聚合逻辑 + 报告标注。
- **人工 later**：教研定义技能簇（哪些细 KP 可成簇）。

---

## 7. K7 · difficulty_prior / cog_level 名不副实

### 现状（代码实证）

两个属性"挂着没用"：

1. **`difficulty_prior`**：`routes.py:1058` 自注 "difficulty_prior 当前未参与掌握度计算，无即时影响"。它只作题目属性(`templates.py:61` difficulty_est)，叫 prior 却不当先验。`kb.yaml` 头注也说"班级得分率数据到位后仅作参考"--但数据到位后也没用上。

2. **`cog_level`**：证据事件记录认知层级(`evidence.py:101`)，`mastery.py:28-42` 的 `get_events` 支持 `cog_level` 过滤参数，但主评估路径 `assess_student_kps`(`weakness.py:119`) 调 `get_events_batch` 不传 cog_level，`mastery_of_events` 也不分层--等于记了不用。一个学生可能"识记"加法法则（知道规则）但"应用"失败（不会算），两者混在一个掌握度里。

### 方案

**A. difficulty_prior 接入贝叶斯先验（代码 now，与 K3 配合）** -- 掌握度估计加先验收缩：

```python
# mastery.py -- mastery_of_events 加可选先验
def mastery_of_events(events, as_of, prior=None, prior_strength=5.0):
    # 原加权平均 likelihood
    ...
    if prior is not None:
        # 贝叶斯收缩：mastery = (likelihood·n + prior·prior_strength) / (n + prior_strength)
        # 数据少时偏向 difficulty 先验（1-difficulty ≈ 预期掌握度），数据多时回归观测
        mastery = (num/(den) * n + prior * prior_strength) / (n + prior_strength) if den>0 else prior
    ...
```

`prior = 1 - kp.difficulty_prior`（难度低 -> 先验掌握度高）。数据少时用先验兜底（避免 2 证据的极端值），数据多时回归观测。与 K3 边权精炼共享贝叶斯框架。

**B. cog_level 分层掌握度（代码 now，按需）** -- `cog_levels_expected` 多层的 KP（如 M7A-103 数轴 [理解,应用]），分别算各层掌握度：

```python
# weakness.py -- cog_levels_expected 长度 >= 2 时，算分层
if len(kp.cog_levels_expected) >= 2:
    per_cog = {cog: mastery_at(session, sid, kp_id, as_of, cog_level=cog) for cog in kp.cog_levels_expected}
    a.per_cog_mastery = per_cog  # 报告可展示"识记 0.8 / 应用 0.5"
```

报告："该生能复述数轴三要素（理解 0.82），但不会用数轴比较大小（应用 0.45）"--这正是教师需要的诊断粒度。

**C. 死结构清理（人工 later）** -- spiral 关系若 K1 后仍无消费场景，从 schema 删除或归档；contains 边若确认与 `chapter` 字段冗余，前端改用 chapter 字段分组，contains 边降级为可视化辅助。

### 验收
- `difficulty_prior` 接入掌握度先验：数据少时收缩向 `1-difficulty`，数据多时回归观测。单测：2 证据 + 极端分（0/10），先验把掌握度从 0.0 拉向 0.6+（difficulty=0.4）。
- 多层 cog 的 KP 报告展示分层掌握度。
- spiral/contains 清理决策列入本文档（即使暂不执行）。

### 执行性
- **代码 now**：difficulty 先验接入（与 K3 共享框架）+ cog 分层展示。
- **人工 later**：spiral/contains 清理决策（教研确认是否有消费场景）。

---

## 8. 落地批次与取舍

### 第一批 · 低成本高收益（激活存量，不增维护负担）— ✅ 已落地（2026-08-08）

| 项 | 改动 | 收益 | 成本 | 落地状态 |
|---|---|---|---|---|
| **K2 floor 分层** | config 常量 + weakness 派生（`effective_floor`） | 直接压 FP（综合题假正常 / 识记题假薄弱） | 低，零运行时成本 | ✅ 误报 0.208 → **0.198**（-5%） |
| **K4 前向视图** | graph `descendants` 方法 + 诊断单预警 | 干预前瞻性，逻辑现成 | 低，BFS 缓存 | ✅ 单测覆盖 |
| **K1 confusable 激活** | graph 加载 + `_confusable_pair` 归因 | 补"易混淆"错因维度 | 低代码 + 教研补边 | ✅ 单测覆盖（补边仍待教研） |
| **K7-A difficulty 先验** | mastery 加贝叶斯收缩 | 数据少时兜底，与 K3 共享框架 | 中 | ⚠️ 代码落地但默认关（实证冲突，见下） |

> **K7-A 落地说明**：全局收缩与 floor 判定结构性冲突——`prior_strength=5` 相对 n=2 证据权重 71%，会把低证据正常学生（真实 0.65-0.70）压过派生底线，金标误报 0.166 → 0.415。故按"金标不退化"硬约束，先验**默认关闭**（`SC_MASTERY_PRIOR_STRENGTH=0`），机制与单测保留，待大规模验证有效后转正默认（与 MIN=2/strict 先验证后转正的纪律一致）。

### 第二批 · 中成本高收益（数据到位后）— ✅ 已落地（2026-08-08）

| 项 | 改动 | 收益 | 成本 | 落地状态 |
|---|---|---|---|---|
| **K3 边权精炼** | `scripts/refine_edge_weights.py` 批处理 + 人工确认 | 提 root-hit（0.863 -> ?），图谱随数据长准 | 中，需真实数据 ≥8 样本/边 | ✅ 脚本+单测（真实数据后生效） |
| **K5 节点重要度** | `importance` 字段 + 报告排序 + 全局薄弱加权 | 报告优先级，干预决策 | 低代码 + 教研标注 | ✅ 模型/API/前端/kb.yaml 40 点标注完成 |
| **K7-B cog 分层** | 分层掌握度 `per_cog_mastery` + 报告 | 诊断粒度匹配教师思维 | 中，数据充足才有意义 | ✅ 代码+单测（报告展示） |

> **第二批落地说明（2026-08-08）**：
> - **K3**：`scripts/refine_edge_weights.py` 复用 suspect_edges 相关性逻辑，贝叶斯收缩 `posterior=α·prior+(1-α)·|corr|`，α=n/(n+10)；`|corr|<0.2 且 n≥8` 标「待复核」建议降权到 0.3，`corr>0.5` 标「确认」。只产 diff 报告（`--out`），不自动改图。合成数据端到端：45 条边有样本、23 条建议复核（合成数据掌握度相关性天然弱，属预期——脚本「数据说话」，教师可拒绝）。
> - **K5**：`KnowledgePoint.importance`（基础/核心/拓展，默认核心），loader 校验三档；`_kp_brief`/create/update API + 前端 KpDetailEditor 下拉全链路；kb.yaml 40 个 grade7 KP 已按教学常识标注初稿（基础 15/核心 16/拓展 9，教研可复核）。报告薄弱清单按 基础>核心>拓展 排序、同级别按缺口降序；全局薄弱判定按重要度加权（基础 ×1.5 / 拓展 ×0.5），避免「拓展题做不好」被当「全局基础差」。
> - **K7-B**：`cog_levels_expected` 长度≥2 的 KP 按证据 cog_level 分维算 `per_cog_mastery`（复用预取事件，零额外查询），报告展示「认知层级分层」，揭示「能复述但不会用」的层级断层。仅展示不参与薄弱判定。
> - **量化**：大规模随机模拟（150 人 × 12 场 × 6 种子）与第一批持平——召回 0.899 / 误报 0.198 / 根源 0.863 / 遗忘 0.922 / 覆盖 1.000，零退化。K5/K7-B 改的是报告呈现与加权，不碰核心判定路径。138 测试全绿（含 stress 5 项、金标 8 项）。

### 第三批 · 按需

| 项 | 改动 | 收益 | 成本 |
|---|---|---|---|
| **K6 软聚类** | 簇定义 + 聚合兜底 | 细章节饥饿兜底 | 中高，簇定义需教研 |
| **K7-C 死结构清理** | 删 spiral/降级 contains | 减维护面 | 低，但丢未来选项 |

### 核心取舍

1. **激活优先于新增**：K1/K2/K4/K7 都是消费已有语义（confusable/floor/descendants/difficulty），不增节点不增边，维护负担不涨。这是性价比最高的方向。

2. **K3 是质量天花板**：唯一让图谱"从静态草稿变成随数据长准的活图谱"的机制。但需真实数据 + 人工确认兜底，是第二批的核心。与 K7-A 的贝叶斯先验共享框架，建议一起做。

3. **不删死结构，先激活**：K7-C 的清理放到最后。spiral/contains 若 K1/K4 激活后仍无消费场景再删，避免丢未来选项。

4. **效率底线**：所有改动不改图谱规模（仍 53 节点 networkx 内存遍历），不增运行时查询（前向 BFS 缓存、边权精炼是批处理）。K6 软聚类是唯一增运行时逻辑的，且仅在饥饿时触发。

---

## 9. 验收总览

每项落地后跑大规模随机模拟（`scripts/effectiveness_largescale.py`）量化验收，对照基线（生产默认 MIN=2/strict/0.7）：

| 指标 | 基线（v0） | 第一、二批落地后（2026-08-08） | 方向 |
|---|---|---|---|
| 薄弱召回 | 0.887 | **0.899 ± 0.023** | 不退化 ✓（微升） |
| 正常误报 | 0.208 | **0.198 ± 0.019** | **K2 下降 ✓（-5%）** |
| 根源命中 | 0.863 | 0.863 ± 0.028 | 持平（K3 需真实数据） |
| 遗忘识别 | 0.909 | 0.922 ± 0.079 | 不变 ✓ |
| 覆盖率 | 1.000 | 1.000 | 持平 |

金标 8 项 + stress 5 项不退化 ✓；`tests/test_kb_improve.py` 11 项单测全绿；**138 测试全绿**。

新增度量：
- K1：易混淆归因产出数（植入混淆对时检出率）。
- K4：前向预警覆盖率（薄弱 KP 有后代时是否产出预警）。
- K7-A：低证据 KP 掌握度稳定性（先验收缩后方差下降）。

金标基线（`test_gold.py` 8 项）不退化是硬约束；新增项各配单测。
