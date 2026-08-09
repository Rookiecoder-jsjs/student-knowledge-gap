# 前端完全重设计 · 案头 Workbench + 考试为主线

> 2026-08-09。推翻昨天（08-08）落地的「绿洲/Biophilic Calm」方向，做全新重设计。
> 边界：后端 API 不变、技术栈不变（React19 + Vite + Tailwind4 + framer-motion + react-router7）、保留全部功能与页面。
> 硬约束（README §设计约束）：不排名 · 证据可溯+置信可见+可否决 · 成长框架措辞 · LLM「模型生成」标注+预览 · 未教不判薄弱。

---

## 0. 决策摘要

| 维度 | 结论 |
|---|---|
| 视觉 | **案头 Workbench**：暖灰中性底 + 单一松青 #0f766e + 等宽数字 + 紧栅格 + 利落阴影。替换绿洲的暖纸+墨绿+大圆角柔影。 |
| IA | **考试为主线**：4 导航 → 3（工作台/考试/学生）；「考试」做成 5 阶流水线工作区（stepper 串联 建卷→审核→采集→提交→报告）。保留全部页面与功能，只是连起来。 |
| 三痛点对照 | 工作流割裂→5 阶 stepper 主线；导航层级不清→3 项导航+stepper 自表达；信息密度失衡→紧栅格 14px+流水线卡片+报告 TOC。 |

---

## 1. 视觉系统：绿洲 → 案头 Workbench

### 1.1 色彩 token（重写 `index.css` @theme）

| token | 绿洲(旧) | 案头(新) | 说明 |
|---|---|---|---|
| `--color-canvas` | #f4f2ea | **#f6f6f4** | 暖中性底（去绿调，比纯灰暖、比绿洲冷） |
| `--color-surface` | #fffdf8 | **#ffffff** | 纯白卡片（更精密） |
| `--color-surface-2` | #faf7ee | **#f1f1ee** | 嵌套/hover |
| `--color-line` | #e3ddce | **#e5e5e1** | 中性发丝线 |
| `--color-ink` | #1f261e | **#1c1c1a** | 近黑中性（去绿） |
| `--color-ink-soft` | #515c4a | **#575753** | 次级文字 |
| `--color-ink-faint` | #67725a | **#787872** | 三级文字（白底 ≥4.5:1） |
| `--color-accent` | #2f6b4f | **#0f766e** | 松青·唯一主色 |
| `--color-accent-deep` | #215039 | **#0c5d56** | 按下/深态 |
| `--color-accent-soft` | #e6efe4 | **#dcefec** | 选中/高亮淡底 |
| `--color-warn` | #b45309 | #b45309 | 不变 |
| `--color-danger` | #b3403a | #b3403a | 不变 |

- 移除 `sage` / `clay` 辅色（绿洲专属）。状态仍由 accent/warn/danger 承担；语义映射不变（高→accent-soft，中→surface，低→danger-soft）。
- 不引入蓝/紫/粉；松青为唯一主色。

### 1.2 圆角（收紧 → 精密感，区别于绿洲的有机大圆角）

| 用途 | 绿洲 | 案头 |
|---|---|---|
| 卡片/弹窗 | 16px | **10px** |
| 按钮/输入/下拉 | 12px | **8px** |
| 徽标/小标签 | 8px | **6px** |
| 进度条/药丸 | 9999 | 9999 |

### 1.3 阴影（中性、低、利落，非暖绿柔影）

```
--shadow-soft:  0 1px 2px rgba(0,0,0,.04), 0 1px 3px rgba(0,0,0,.06);  /* 卡片静息 */
--shadow-lift:  0 4px 12px -2px rgba(0,0,0,.08);                         /* 悬停 */
--shadow-float: 0 12px 32px -8px rgba(0,0,0,.14);                        /* 弹窗/浮层 */
```

### 1.4 字体（等宽数字 = Workbench 核心特征）

- UI sans：`Outfit` → **`Inter`**（tabular-nums 更优，加 `@fontsource/inter`，与现有 `@fontsource/outfit` 同族低风险）；若离线无法装，退回 system-ui + `font-variant-numeric: tabular-nums`。
- mono：`JetBrains Mono` 不变（KP 编码 / 得分 / 数据）。
- 正文 15px → **14px**（更密）、行高 1.55；标题 600 tracking-tight；最小 12px 不变。
- **所有数据元素（得分、计数、掌握度%、进度）强制 `tabular-nums`**，编码用 mono。

### 1.5 动效（利落，非有机慢柔）

| 项 | 绿洲 | 案头 |
|---|---|---|
| 缓动 | [0.22,1,0.36,1] | **[0.16,1,0.3,1]** |
| 路由过渡 | 0.26s | **0.18s** |
| stagger | 0.05s | 0.04s（>16 项整体淡入，保留） |
| 卡片悬停 | -translate-y-1 + shadow-lift | **-translate-y-0.5 + shadow-lift**（更克制） |

保留 `prefers-reduced-motion` 静态降级。

### 1.6 文件

- 重写 `design-system/sc-teacher/MASTER.md`：新方向、新 token、组件规范、a11y 清单（继承绿洲全部已修项）。
- 重写 `index.css` @theme + 基础样式：移除暖底径向光晕 → 中性底；焦点环、骨架扫光改中性灰。

---

## 2. 信息架构：考试为主线

### 2.1 导航（4 → 3）

侧栏 3 项：**工作台 / 考试 / 学生**。班级切换移到顶栏（更醒目、常驻）。知识库降为全局次级入口（侧栏底/顶栏）。
**质量分析从一级导航移除** → 成为考试流水线第 5 阶（仍保留 `/c/:cid/quality` 直达入口）。

### 2.2 考试工作区 ExamWorkspace（中心件）

新增 `ExamWorkspace.tsx`，包裹考试级路由，顶部常驻 **5 阶 stepper**：

```
建卷 ●── 审核 ●── 采集 ●── 提交 ○── 报告 ○
```

当前阶段高亮、已完成填实、已解锁阶段可点击跳转、未达阶段置灰。

**阶段映射（全部功能保留，只重排）：**

| 阶 | 路由 | 组件 | 说明 |
|---|---|---|---|
| 1 建卷 | `/c/:cid/exams/:eid` | 新 `TemplateView` | 题目结构、满分、知识点标注概览（新建走 `/new` 创建后跳入阶段2/3） |
| 2 审核 | `/c/:cid/exams/:eid/review` | `Review`（重构） | 聚焦审核队列（低置信标注+低置信得分），模板概览移到阶段1 |
| 3 采集 | `/c/:cid/exams/:eid/collect` | `Collect`（重构） | 矩阵 + 批量拍照 + 手工录入；提交动作移到阶段4 |
| 4 提交 | `/c/:cid/exams/:eid/commit` | 新 `CommitView` | 就绪检查 + 二次确认 + 提交结果摘要（从 Collect 抽出 commitExam） |
| 5 报告 | `/c/:cid/exams/:eid/report` | `Quality`（预设 examId） | 保留 narrative 开关/预览/导出 |

`/c/:cid/exams` 考试列表改为**流水线卡片**：每场考试显示阶段进度点（●◐○）+ 下一动作 + 直达当前阶段。

### 2.3 路由（`App.tsx`，保留全部 12 路由语义，重排为工作区嵌套）

```
/                        ClassPicker
/wizard                  Wizard
/kb                      Kb
/c/:cid                  Shell > Overview（工作台）
/c/:cid/exams            Shell > Exams（流水线卡片列表）
/c/:cid/exams/new        Shell > ExamNew（创建后跳工作区阶段2/3）
/c/:cid/exams/:eid       Shell > ExamWorkspace > TemplateView（阶1）
/c/:cid/exams/:eid/review   ExamWorkspace > Review（阶2）
/c/:cid/exams/:eid/collect  ExamWorkspace > Collect（阶3）
/c/:cid/exams/:eid/commit   ExamWorkspace > CommitView（阶4）
/c/:cid/exams/:eid/report   ExamWorkspace > Quality（阶5）
/c/:cid/students         Shell > Students
/c/:cid/students/:sid/diagnosis  Shell > Diagnosis
/c/:cid/students/:sid/mastery    Shell > Mastery
/c/:cid/quality          Shell > Quality（直达入口：选考试→跳 report 阶）
```

React Router v7 嵌套路由 + `<Outlet />`：ExamWorkspace 渲染 stepper + `<Outlet/>`，各阶段为子路由。

### 2.4 三痛点对照

- **工作流割裂** → 5 阶 stepper 把考试生命周期串成一条主线；阶段间「下一步」按钮无缝推进；不再在 5 个无关一级页间跳。
- **导航层级不清** → 3 项清晰导航 + 工作区 stepper 自表达进度；学生诊断 2 跳直达（学生→诊断单）。
- **信息密度失衡** → 紧栅格 14px + 流水线卡片 + 报告页 TOC/统计条；价值页不再是一张空 markdown 卡。

---

## 3. 组件改造

- `components/ui.tsx`：Button/Card/Badge/Input/Modal/PageHeader/SectionTitle 全部改 Workbench 样式（收紧圆角、中性阴影、等宽数字）。**新增**：`Stepper`（流水线阶段）、`StatTile`（KPI 统计块）、`PipelineCard`（考试阶段卡片）、`TOC`（报告目录）、`StatusDot`（●◐○）。保留 `Modal` a11y（焦点圈定+Esc+初始聚焦+归还焦点）。
- `components/motion.tsx`：利落缓动；保留 stagger cap 与 reduced-motion。
- `components/Shell.tsx`：3 项导航、更窄侧栏（w-56→w-52）、顶栏班级切换、面包屑。
- **新增** `components/ExamWorkspace.tsx`：stepper + 阶段切换 + `<Outlet/>`。
- **新增** `pages/TemplateView.tsx`（阶1）、`pages/CommitView.tsx`（阶4）。
- `Review`/`Collect`/`Quality` 重构为阶段面板：去掉与工作区重复的 PageHeader，复用工作区上下文（exam 名/题数显示在 stepper 区）。

---

## 4. 密度与价值页强化

- **工作台 Overview**：顶部统计条（考试数/待办/进度）+ 流水线卡片（每场考试阶段点+下一动作）+ 待办 + 教学进度。一屏看清「现在该干什么」。
- **报告（Quality/阶5）**：左栏 sticky `TOC`（从 markdown 标题提取）+ 顶部统计条（已提交/题数，来自 `listExams`/`examResponses`，**不编造数据**）+ 编辑式排版。让 markdown 报告成为有结构的一等出版物。
- **诊断 Diagnosis**：保持 报告+薄弱卡+归因 双栏；强化「先进步后缺口」成长框架排序；归因卡否决入口保留。
- **掌握度 Mastery**：加汇总头（高/中/低各档数量）+ 章节网格（色阶仅表达高低，不横向比较）。

---

## 5. 可达性（继承绿洲全部修复，不回退）

Modal 焦点圈定+Esc；文件上传 sr-only+可见按钮触发；toggle `aria-pressed`；分段控件 tab 语义；表格 `overflow-x-auto`；诊断/掌握面包屑；12px 最小字号；焦点可见环。**新增组件（Stepper/StatTile/PipelineCard/TOC）同样遵循**：stepper 用 `<ol role="list">` + `aria-current`，阶段跳转为真实链接/按钮。

---

## 6. 硬约束核对

- 不排名：学生名单原序（Students 不变）✓
- 证据可点+置信可见+否决：诊断归因卡（overrideAttribution）✓
- 成长框架措辞：诊断「先进步后缺口」、缺口表述为「下一步」✓
- LLM「模型生成」+预览：Quality/Diagnosis narrative 开关+预览 ✓
- 未教不判薄弱：gates「未学到/数据不足」显示 ✓

---

## 7. 实施分期

- **P1 设计系统地基**：重写 `MASTER.md` + `index.css` token + `ui.tsx` 原语 + `motion.tsx`（+ `@fontsource/inter`）。全站即时换肤。校验 `tsc --noEmit` + `oxlint src` + `vite build`。
- **P2 IA 工作区**：新 `Shell`(3 导航) + `ExamWorkspace`+`Stepper` + `TemplateView`(阶1) + `CommitView`(阶4) + `App.tsx` 路由重排 + `Review`/`Collect`/`Quality` 阶段化 + 考试列表流水线卡片。
- **P3 密度打磨**：Overview（统计条+流水线卡片）/ Quality（TOC+统计条）/ Mastery（汇总头）/ 诊断（成长框架排序）强化。
- **P4 校验**：`tsc --noEmit` + `oxlint src` + `vite build` 全过；`codegraph sync` + `codegraph status`（CLAUDE.md 要求）；更新记忆 `frontend-redesign-oasis` → 新方向。

---

## 8. 不做

- 不动后端 API 与业务逻辑。
- 不改技术栈（仅加一个字体包）。
- 不删功能/页面（Quality 路由保留直达）。
- 不引入蓝/紫/粉非语义色（松青为唯一主色）。
