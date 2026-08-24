# 📊 学生知识薄弱点分析归因系统（sc）

一个面向中学教师的**学情诊断工具**🎓。长期追踪学生每次考试/练习表现，估计各知识点的掌握度，归因薄弱点的成因，并生成可直接用于讲评课、家长会、教务汇报的文档，通过复测形成闭环。考试一提交，班级报告与个人诊断就**自动生成并落库**——一次生成，随时回看 💾。试点学科是初一数学（人教版七上）📐——不过年级只是知识库数据层参数，换个 YAML 就能切换学科。

> 📌 设计依据、改进方案、诊断有效性验证、知识图谱改进等设计文档仅保留本地（`docs/`），不入库（唯一例外：`docs/architecture-fix-plan.md` 随架构方案交付入库）。

---

## 🎯 它在解决什么问题

教师批改完试卷，手里往往只有一张总分。要把"谁薄弱、为啥薄弱"讲清楚，靠的是经验猜和手工算——既耗时间，也难追溯。本系统把这条链路做实：

```
知识库 -> 采集（Excel/拍照）-> 证据事件 -> 掌握度推导 -> 双基准薄弱判定 -> 归因（可证伪）-> 质量分析文档 / 个人诊断单
```

## ⚠️ 它能做什么、不能做什么（能力边界，必须守住）

- 🔬 只诊断**知识维度**：动机、家庭、考试焦虑、师生关系等不可见因素不在范围；
- 🤔 所有归因输出定位为"**带证据的方向性假设，供教师确认**"，不是诊断结论；
- 🎚️ 归因精度受标注质量与失分归属约束（端到端约六七成），产品形态与之匹配：**证据随行、置信度可见、教师可否决、诊断题可证伪**。

## 📦 第一交付物

教师真实工作物是**文档**📄，不是仪表盘。系统优先产出：

1. 📝 **一键考后质量分析文档**（班级，可编辑、可导出）——替代教师已有工作；
2. 🧑‍🎓 **个人诊断单**（薄弱点 + 证据 + 建议）；
3. 📈 仪表盘为探索性辅助（概览、掌握度曲线、学生画像）；
4. 💾 **提交即自动生成**——考试提交后，班级质量报告 + 全班学生诊断自动落库并关联该场考试，一次生成、永久查看；AI 解读段首次查看时生成并缓存。

---

## 🏗️ 系统架构

五层管线，四条架构不变量（违反即设计缺陷）：

```
┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌────────┐  ┌────────────┐
│ 知识库层  │->│  数据采集层   │->│ 知识追踪层 │->│  归因层  │->│  报告层     │
│ 知识图谱  │  │ Excel/拍照->   │  │ 证据事件-> │  │ 假设+  │  │ 质量分析 +  │
│ 版本化   │  │ 审核->提交     │  │ 实时推导  │  │ 证伪   │  │ 个人诊断单  │
└──────────┘  └──────────────┘  └──────────┘  └────────┘  └────────────┘
```

**四条不变量：**

1. 🔒 **分析层只读已提交数据**——采集是状态机（上传->解析中->待审核->已提交），草稿/待审数据隔离在暂存区；
2. 🧮 **派生状态只推导、不存储**——掌握度、归因由不可变证据事件实时推导（derive-on-read），教师改标注/分数后下游自动正确，无陈旧快照；
3. 🚦 **LLM 输出必经闸门**——任何 LLM 输出进分析前须经人工审核或封闭集合 Schema 校验，LLM 负责解析/标注/渲染，判断权在人与确定性逻辑；
4. 🚫 **报告数字零幻觉**——叙述性报告用模板槽位生成，数字与结论由系统注入，LLM 只写连接性文字。

---

## 🛠️ 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 · FastAPI · SQLAlchemy 2.0 · SQLite（MVP，可迁 PostgreSQL）· networkx（知识图谱内存遍历）· openpyxl（Excel）|
| 前端 | Vite · React 19 · TypeScript · Tailwind CSS 4 · react-router 7 · framer-motion · react-markdown · Inter（等宽数字）|
| LLM | provider 无关接口层（vision + text 双能力），云端 API；试点用 DashScope `qwen3.7-flash` |
| 知识库源 | YAML + Git，导入脚本入库 |
| 图计算 | 关系表存储 + networkx 内存遍历（百级节点，无需图数据库）|
| 部署 | Docker（后端/前端镜像）+ docker compose 三服务编排（backend / frontend / backup）· nginx `/api` 反代 · 单 uvicorn 进程（架构不变量）· SQLite 热备 |

---

## 📁 项目结构

```
sc/
├── backend/                  # Python 后端（FastAPI，五层管线，56 端点）
│   ├── app/
│   │   ├── api/routers/      #   路由（org / ingestion / kb / analysis / reports）+ deps（依赖注入）
│   │   ├── ingestion/        #   采集：excel / photo / batch（批量）/ pii / commit / templates
│   │   ├── kb/               #   知识库：loader（YAML->DB）/ graph / resolver（active 版本）/ edit / versioning / compatibility
│   │   ├── pipeline/         #   追踪与归因：evidence -> mastery -> weakness -> attribution（含诊断题证伪、归因读视图）
│   │   ├── queries/          #   只读聚合查询（classes_overview 等）
│   │   ├── reports/          #   compute/render 分层：quality_model|quality_render / diagnosis_model|diagnosis_render / auto_generate / narrative（LLM 解读）/ labels
│   │   ├── llm/              #   provider 无关客户端 + prompts + gateway（文本闸门）+ circuit（熔断器）
│   │   ├── labels_source.py  #   枚举标签单一真源（codegen 出前端 labels.ts，防两处漂移）
│   │   ├── models.py schemas.py config.py db.py observability.py main.py（含 /health + /ready 探针）
│   ├── kb/math/grade7/kb.yaml #   知识库（人教版七上，待教研审核）
│   ├── tests/                #   单元测试（含有效性修复、归因抑制、证伪闭环、P25 误报、健康探针）
│   ├── simulator/            #   合成模拟器 + 金标端到端断言 + 压力金标 + 大规模随机模拟
│   ├── scripts/              #   run_demo / effectiveness_multiround / effectiveness_largescale / audit_kb_edges / backup_db / backup_loop.sh
│   ├── output/               #   demo 产出（质量分析、个人诊断单）
│   ├── Dockerfile            #   后端镜像（单 uvicorn 进程，架构不变量；见 DEPLOY.md「单进程原理」）
│   └── .env                  #   LLM 与质量开关配置（勿入库）
├── frontend/
│   ├── app/                  #   教师端 Web（Vite + React + TS + Tailwind，案头 Workbench 风格）
│   │   ├── src/{pages,components,lib}/
│   │   ├── Dockerfile        #   前端镜像（node 构建 -> nginx 托管 dist + /api 反代）
│   │   └── nginx.conf        #   /api 前缀剥离反代 backend（与 Vite dev 代理等价）
├── deploy/
│   └── docker-compose.yml    #   单机「基础可靠」部署编排（backend / frontend / backup 三服务 + 卷）
├── DEPLOY.md                 #   部署与运维文档（备份恢复三步法 / 单进程原理 / 演进路径）
└── .venv/                    #   Python 3.11 虚拟环境（项目根，backend 共用）
```

> ⚠️ 设计文档（`docs/`、`frontend/design/`、`design-system/`）仅本地保留，已加入 `.gitignore` 不入库（`docs/architecture-fix-plan.md` 为例外，已入库）。

---

## 🚀 快速开始

后端命令在 `backend/` 目录下执行（先激活 Python 3.11 虚拟环境）：

```bash
cd backend
python -m pytest tests simulator                  # 🧪 单元 + 金标 + 压力断言（187 项）
python scripts/run_demo.py                        # 🎬 合成班级全流程 -> output/*.md
python scripts/effectiveness_largescale.py        # 🌊 大规模随机有效性测试（150 人 × 12 场 × 6 种子）
python -m uvicorn app.main:app --reload           # ⚙️ 启动 API（Swagger 交互文档 /docs）
```

前端（另开终端）：

```bash
cd frontend/app
npm install
npm run dev                                       # 🖥️ 启动前端开发服务器（/api 代理到后端）
```

> 🐳 **容器化部署**（基础可靠 / 单机，三服务编排 backend + frontend + backup）见 [DEPLOY.md](DEPLOY.md)。

## ⚙️ 配置（backend/.env）

```
SC_LLM_PROVIDER=          # 供应商，如 dashscope / openai / anthropic
SC_LLM_API_KEY=
SC_LLM_BASE_URL=
SC_LLM_MODEL=             # 默认模型
```

视觉（试卷解析）与文本（报告解读）两类能力可分别覆盖：`SC_LLM_VISION_MODEL` / `SC_LLM_TEXT_MODEL`。未配置时 provider 层默认 mock 并显式报错，可在无密钥环境下测试与演示 🧪。

**质量与审核开关**（均默认关闭，保持既有工作流；试点/生产按需开启）：

```
SC_KB_STRICT_ACTIVE=       # =1 时无 active 知识库版本则报错（不兜底 draft，避免分析跑在未审图谱上）
SC_EVIDENCE_MIX_PENALTY=   # 0~1，多 kp 混合题失分归属折扣强度（0 关闭；1 启用全折扣）
SC_TAG_REVIEW_SAMPLE_RATE= # 0~1，批量批准标注时高置信题的抽样保留率（0 关闭；建议 0.1）
```

**诊断有效性参数**（已落地为生产默认，经大规模随机模拟验证；env 可覆盖回退）：

```
SC_MIN_EVIDENCE_COUNT=2    # 证据题数门槛，< 此值判"数据不足"。默认 2（期中即可用）；=3 更保守
SC_WEAKNESS_MODE=strict    # 薄弱判据：strict=仅贴近底线才触发 P25（消结构性误报）；standard=相对判据
SC_FORGET_PEAK_THRESHOLD=0.7  # 遗忘检测：历史峰值需 ≥ 此值才算"曾经掌握"
```

这三个参数是系统"诊断准不准"的核心旋钮 🎛️。默认值不是拍脑袋定的——是用 150 人 × 12 场考试 × 6 个随机种子的模拟跑出来的（见下文「验证体系」）：`MIN=2` 让系统不用熬到期末才出诊断，`strict` 把"全班都挺好却硬挑出 25% 薄弱"的误报砍掉 11%，召回和根源命中率不掉。想退回最保守的基线，设 `SC_MIN_EVIDENCE_COUNT=3 SC_WEAKNESS_MODE=standard` 即可。

**部署相关变量**（`SC_CORS_ORIGINS` / `SC_BACKUP_*` / `SC_DATABASE_URL` 容器化语义等）见 [DEPLOY.md](DEPLOY.md) 与 `backend/.env.example`。

---

## 📡 核心功能与端点

共 **56 个端点**（启动后可在 Swagger 交互文档查看）。运维探针：`GET /health`（liveness，进程存活）与 `GET /ready`（readiness——DB 可达即 200；LLM 熔断仅标 `degraded:true`，DB 不可达 503，供编排器自愈）。

**📚 知识库**
- 导入与版本：`POST /kb/import`、`GET|POST /kb/versions`、`PATCH /kb/versions/{id}`、`GET /kb/versions/{id}/compatibility`
- 运行时编辑（kb-edit，导入后可在前端 `/kb` 页改，无需重导 YAML）：知识点 CRUD `GET|POST /kb/kps`、`GET|PATCH|DELETE /kb/kps/{id}`；关系 CRUD `GET|POST /kb/relations`、`PATCH|DELETE /kb/relations/{id}`
- 高杠杆参数（mastery_floor / difficulty_prior）支持 `?preview=true` 干跑，返回薄弱人数变化再落库
- 辅助标注：`POST /kb/suggest-question-tags`（题干 -> 闭集知识点推荐，不落库，教师审核后才建卷）
- 可疑边反查：`python -m scripts.audit_kb_edges --class-id N`（检测前置边两端掌握度低相关，让 LLM 起草图谱的错边可观测）

**🏫 组织与考试**
- `POST /schools`、`POST /schools/{id}/classes`、`POST|GET /classes/{id}/progress`（教学进度）
- `POST|GET /exams`、`POST /exams/{id}/import-excel`（Excel）、`POST /exams/{id}/manual`（表格录入）、`POST /exams/{id}/commit`（提交，触发分析 + 题库飞轮，并自动生成班级报告 + 全班学生诊断落库）

**📷 拍照录入（两阶段）**
- 阶段A 试卷模板：`POST /exams/photo-template`（解析题目结构 + LLM 知识点标注）-> `POST /exams/{id}/approve-tags`（教师审核全卷；抽样模式下低置信/抽样题保留待逐题确认）
- 阶段B 学生卷：`POST /exams/{id}/photo-response`（每人一卷，仅抽得分/选项）-> `GET /exams/{id}/review-queue`（低置信题复核，返回 `review_reason`）-> `commit`
- 批量录入：`POST /exams/{id}/photo-batch` -> `GET /exams/{id}/batch-jobs` / `GET /batch-jobs/{id}` -> 逐张 `POST /batch-items/{id}/{assign|retry|discard}`

**📈 分析产出（get-or-generate：有已存直接返回，无则补生成落库）**
- 班级：`GET /classes/{id}/quality-report?exam_id=`（质量分析文档——提交后已自动生成，秒回；未提交过的考试按需补生成）
- 个人：`GET /students/{id}/diagnosis`（诊断单——默认返回该生**最近一场考试**的已存诊断，随时看；`?exam_id=` 指定某场、`?as_of=` 按日期现算）、`GET /students/{id}/mastery`、`GET /students/{id}/weaknesses`、`POST /students/{id}/attributions`
- 诊断题证伪：`POST /attributions/{id}/verify`（用诊断题证据验证前置缺陷归因预测，证伪 -> `overridden`、证实 -> 记录确认、证据不足 -> inconclusive）
- 归因否决：`POST /attributions/{id}/override`（教师人工否决，跨重跑保留）
- 归因闭环度量：`GET /attributions/closure`（按证伪/证实/无法证伪分布统计诊断验证率与教师否决率，看归因从"纸面假设"走到"被验证"有多远）
- 报告列表与详情：`GET /reports`（可按 `exam_id`/`class_id`/`student_id` 过滤）、`GET /reports/{id}`
- 报告 AI 解读段首次 `?narrative=true` 查看时生成并缓存到库，之后永久可看（仅引用系统已算数字、标注模型与 prompt 版本、失败静默降级）

**🖥️ 前端页面**：3 项导航（工作台 / 考试 / 学生）+ **考试 5 阶流水线工作区**（建卷 → 审核 → 采集 → 提交 → 报告，顶部 stepper 串联，告别页面跳来跳去）。提交成功后提示「已自动生成班级报告 + N 份学生诊断」并直达报告；诊断页默认展示最近一场考试的已存诊断，可随时选日期回看任意时点（报告与弱项面板同一时间基准）。视觉为「案头 Workbench」🎨——暖灰中性底 + 单一松青主色 + 等宽数字 + 紧栅格，设计系统源文件 `design-system/sc-teacher/MASTER.md`（本地）。含选班级、首次使用向导、知识库编辑等共 14 个路由。

---

## ✅ 验证体系

**测试**：187 项（`backend/tests/` 单元 + `backend/simulator/` 金标与压力断言）🧪。

```bash
cd backend && python -m pytest tests simulator
```

### 三层验证，从"能跑"到"真的准"

**第一层 · 合成金标**（`simulator/test_gold.py`）：植入已知薄弱点，跑通整条管线，断言能检出来。这是最基本的安全网——换模型、改 prompt 必跑，防静默退化。当前基线：

| 指标 | 基线 |
|---|---|
| 薄弱召回 | ≥ 0.80 |
| 根源命中 | 0.96 |
| 遗忘识别 | 3/3 |
| 共性标记 | 1.00 |
| 误报 | ~0.21 |

**第二层 · 有效性深挖**：金标能过，不代表真的有效——合成模拟器用的是同一套假设，存在"用同源逻辑自证"的循环验证嫌疑 🤔。于是做了几件较真的事（过程与结果见本地 effectiveness 文档）：

- 🍽️ **数据饥饿**：发现默认 `MIN=3` 下整学期只有期末大考才出诊断，覆盖率卡在 0.40——系统是"期末回顾"而非"持续诊断"。
- 📉 **P25 结构性误报**：全班都达标时，按相对位置仍会硬挑出 25% "薄弱"——和"不排名"的产品承诺冲突。
- 🌫️ **遗忘识别不稳**：短时间跨度下，遗忘信号淹没在噪声里，5 个种子 0/3 ~ 3/3 乱跳。

对症修了三处（`MIN` 可配 + low_evidence 护栏、`strict` 模式、遗忘阈值降噪），先以 env 开关形式验证不破基线、132 测试全绿；大规模验证通过后，`MIN=2` / `strict` 已转正为生产默认（见上文「配置」）。

**第三层 · 大规模随机模拟**（`simulator/large_scale.py`）：这是打破循环验证的关键一步 🎯。不再固定"在第 105 个知识点植入薄弱"，而是**每换个种子就随机选 10 个位置植入**——管线必须在完全未知的位置把薄弱找出来、把根源归对。150 人、3 个班、12 场考试跨两学期 10 个月、6 个随机种子：

| 指标 | 结果（mean ± stdev） | 说明 |
|---|---|---|
| 薄弱召回 | 0.887 ± 0.044 | 随机位置也能检出，不是对已知答案的拟合 |
| 根源命中 | 0.863 ± 0.028 | 归因到正确的随机根源，而非噪声祖先 |
| 遗忘识别 | 0.91 ± 0.07 | 长时间尺度（寒假 75 天间隔）下信号清晰，远优于短跨度 |
| 正常误报 | 0.21 ± 0.02 | strict 模式比 standard 低 11%；残余是有限题量的估计噪声底 |

有个意外收获 🎁：遗忘识别在 4 个月的测试里只有 ~0.6（很不稳），拉到 10 个月、有了真实的寒假间隔后跳到 0.91。**短跨度下"没识别出遗忘"不等于"没有遗忘"**——这是时间尺度依赖的，得给系统足够长的观察窗口。

### 🧭 还差什么

合成数据再像也不是真的。系统精度仍为合成基线，**200 题真实人工金标**（教师标注失分归属）是阻塞对外宣称精度的第一优先级，只能人工建。诊断题证伪闭环（`POST /attributions/{id}/verify`）是根本解，但需教师配合出题。北极星指标是**干预提升率**：被干预的薄弱点，复测后掌握度涨没涨 🌟。

---

## 📝 改进记录与已知局限

已落地的设计缺陷改进（落地进度表见本地 improvement-plan.md）：

- **§2.2 图谱可疑边反查**：检测前置边两端掌握度低相关，让 LLM 起草图谱的错边可观测。
- **§2.3 前置强度参与归因**：根源选择用「掌握度缺口 × 前置强度」，关系 weight 不再是死字段。
- **§2.1 知识库版本严格开关**：`SC_KB_STRICT_ACTIVE` + draft 兜底警告，避免分析跑在未审图谱上。
- **§1.4-C 失分归属混合度折扣**：`SC_EVIDENCE_MIX_PENALTY` 对多 kp 混合题降权，减少失分等量污染。
- **§1.4-A 诊断题证伪闭环**：`POST /attributions/{id}/verify` 用诊断题证据验证归因预测，让"可证伪"从纸面承诺变实际闭环。
- **§3.2 高置信标注抽样复核**：`SC_TAG_REVIEW_SAMPLE_RATE` 批量批准时保留低置信与抽样题待逐题确认。
- **§3.3 手工建卷 LLM 辅助标注**：题干 -> 闭集 kp 推荐，前端一键回填。
- **§6 题库飞轮**：提交考试时有标注题目幂等写入 `bank_question`。

**诊断有效性补强**（见本地 effectiveness-validation-plan.md）：

- **全局薄弱抑制**：学生多数知识点都薄弱时，"特定前置根源"解释力下降（更像整体基础问题），前置缺陷归因置信度自动下调并标注，targeted-weak 不受影响。
- **low_evidence 护栏**：证据偏少（< 3 题）的知识点可评估但不下因果归因，避免稀疏数据上造伪因果。原则是"评估从宽、归因从严"。
- **strict 薄弱判据**：P25 相对判据仅在掌握度贴近底线时触发，消除"全班达标仍误报 25%"。已落地为生产默认。
- **大规模随机模拟器**：`simulator/large_scale.py`，每种子随机选薄弱位置，打破循环验证；`scripts/effectiveness_largescale.py` 跑 150 人 × 12 场 × 6 种子。
- **归因证伪闭环度量**：`GET /attributions/closure` 按证伪/证实/无法证伪分布统计诊断验证率与教师否决率。

**知识图谱改进·第一批**（见本地 kb-improvement-design.md）：

- **floor 按认知层级派生**：识记 0.70 / 理解 0.65 / 应用 0.60 / 综合 0.55，综合题不再与识记题共用 0.6 底线。显式标注仍优先。大规模模拟误报 **0.208 -> 0.198**（-5%），召回/根源不退化。
- **前向影响预警**：诊断单对薄弱点提示"可能波及"的下游知识点（直接 / 间接分级），给"先补地基"的干预抓手。
- **易混淆归因**：第四类归因类型——薄弱点的易混伙伴也弱时，归因"概念混淆"而非前置缺陷/遗忘（教研补边后生效面更大，3 条 -> 10-15 条待补）。
- **difficulty 先验**：`mastery_of_events` 支持贝叶斯收缩（能力已落地）。因与 floor 判定存在实证冲突（低证据正常学生被压过底线，金标误报 0.166->0.415），**默认关闭**，`SC_MASTERY_PRIOR_STRENGTH` 按需开启。

**知识图谱改进·第二批**（见本地 kb-improvement-design.md）：

- **节点重要度**：`importance` 字段（基础/核心/拓展），kb.yaml 40 点已标初稿。报告薄弱清单按 基础>核心>拓展 排序（同级别按缺口降序）；全局薄弱判定按重要度加权（基础 ×1.5 / 拓展 ×0.5），避免「拓展题做不好」被当「全局基础差」。前端 /kb 可编辑。
- **边权数据精炼**：`scripts/refine_edge_weights.py` 对每条前置边做贝叶斯收缩（α=n/(n+10)），低相关边（corr<0.2, n≥8）建议降权到 0.3 标「待复核」，只产 diff 报告不自动改图，教师确认后落库。真实数据到位后生效。
- **认知层级分层掌握度**：多层 KP 按证据层级分维（`per_cog_mastery`），报告展示「识记 82% / 应用 45%」揭示"能复述但不会用"的层级断层。仅展示不参与薄弱判定。

**提交后自动生成报告**（一次生成永久查看）：

- 考试提交后自动生成 1 份班级质量报告 + 全班已参加学生各 1 份诊断，落库并关联 `exam_id`，幂等替换不产生重复行。
- 批量证据预取：全班×全 kp 证据一次扫描，班级报告与所有学生诊断共享，30 生诊断从 N 次扫描降到 1 次。
- 查看端点改 get-or-generate：有已存直接返回，无则补生成；AI 解读首次查看生成并缓存到 `narrative_markdown`，不重复调 LLM。
- 诊断页默认最近一场考试的已存诊断，日期选择器回看任意时点；报告生成是 best-effort，失败不影响考试提交本身。
- 批量模拟脚本（run_demo / effectiveness_* / diagnose_root_causes）经 `commit_exam(generate_reports=False)` 跳过，不影响既有工作流。

**已知局限**：失分归属是物理上限（单题得分率无法精确拆分给多 kp），诊断题证伪是根本解法但需教师配合出题；真实人工金标（200 题）尚未建立，系统精度仍为合成数据基线。残余误报 ~0.20 主要是有限题量下掌握度估计的噪声底（边界学生 0.65-0.70 贴近底线），非结构性缺陷，根治需每知识点更多题而非调参。K3 边权精炼的 root-hit 提升需真实数据（每边 ≥8 样本）才能兑现。

---

## 🎨 设计约束（前端必须遵守）

- 🚫 不展示排名（双减）；
- 🔍 每条结论证据可点开、置信度可见、教师可否决；
- 🌱 措辞用成长框架（先进步、后缺口，缺口表述为"下一步"），无绝对化表达；
- 🤖 LLM 解读段必须保留"模型生成"标注与教师预览环节；
- 📚 教学进度未覆盖的知识点绝不判为薄弱（标"未学到"）。

---

## 📄 开源协议

本项目依据 [Apache License 2.0](LICENSE) 授权 ⚖️。相比 MIT，额外提供明确的专利授权与反诉保护。版权署名见 [NOTICE](NOTICE)。
