# 前端交互设计工作区

本目录承接 DESIGN.md §9（产品形态）的前端交互设计。设计稿、页面流程、
线框图放在这里。

**实现状态**：教师端 Web 已在 `frontend/app/` 落地（Vite + React + TS +
Tailwind），本目录的 `pages.md`（线框）与 `api-inventory.md`（30 端点对照）
为实现依据；学生/家长端形态仍待决（DESIGN §16：Web / 微信小程序）。

## 价值兑现顺序（页面优先级依据，DESIGN §9 信任阶梯）

1. **班级共性薄弱点**（明天讲评课就能用）→ 考后质量分析页
2. **个体薄弱清单** → 学生诊断页
3. **归因链** → 诊断页内的可展开证据视图

## 页面清单（按教师工作流）

| 页面 | 核心交互 | 对应 API |
|---|---|---|
| 首次使用向导 | 选学科/年级/教材 → 导知识库 → 补录 2~3 次历史考试（冷启动关键） | `POST /kb/import`、`POST /schools`、`POST /schools/{id}/classes`、`POST /classes/{id}/progress` |
| 考试录入 | 拍照上传（阶段A）或 Excel 上传；展示解析进度与警告 | `POST /exams/photo-template`、`POST /exams`、`POST /exams/{id}/import-excel` |
| 审核台（左图右表） | 模板知识点标注审核（LLM 草稿逐题确认）；异常式审核只看低置信项 | `GET /exams/{id}/review-queue`、`POST /exams/{id}/approve-tags` |
| 学生卷采集 | 逐人拍照（阶段B）或分数网格手工录入 | `POST /exams/{id}/photo-response`、`POST /exams/{id}/manual` |
| 提交与进度 | 待审核/已提交状态、提交后证据条数反馈 | `POST /exams/{id}/commit` |
| 班级质量分析 | 一键生成、可编辑导出、`narrative` 开关 | `GET /classes/{id}/quality-report?exam_id=&narrative=` |
| 学生诊断单 | 进步先行呈现；薄弱点卡片（判据/轨迹/证据数）；归因假设可展开证据与验证方式；教师否决入口 | `GET /students/{id}/diagnosis`、`GET /students/{id}/weaknesses`、`POST /students/{id}/attributions` |
| 掌握度画像 | 知识点掌握度网格/曲线（探索性辅助，优先级最低） | `GET /students/{id}/mastery?as_of=` |

## 交互硬约束（来自设计与合规）

- **禁止排名**：任何视图不得按总分给学生排序（双减）；
- **证据随行**：薄弱点/归因必须能点开看到题目数、最近时间、置信度；
- **教师否决权**：归因卡片必须有"不认可+备注"入口；
- **审核不阻塞初步产出**：待审核数据可出"初步报告"但需显著标注；
- **AI 解读段**：保留"模型生成"标注，家长会等对外材料需教师预览确认步骤；
- **措辞**：成长框架（先进步后缺口），无"差/落后"标签词。

## 参考材料

- `docs/DESIGN.md` §5 审核 UX、§9 产品形态、§13 呈现伦理
- API 实时文档：`backend/` 下启动服务后访问 `/docs`（OpenAPI/Swagger）
- 报告样例：`backend/scripts/run_demo.py` 生成 `backend/output/*.md`
