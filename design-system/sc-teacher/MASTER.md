# 薄弱点分析·教师工作台 - 设计系统源文件（案头 / Workbench）

> 全站唯一视觉真相来源。所有页面、组件、动效必须遵循本文件。
> 方向：精密、利落、数据优先的「教师案头工作台」--暖灰中性底、单一松青主色、
> 等宽数字、紧栅格、利落阴影。不引入 AI 紫蓝；克制；教师拥有最终否决权。
> （2026-08-09 推翻 08-08 的「绿洲/Biophilic Calm」方向重设计。）

---

## 1. 设计原则

1. **成长框架，非评判**：用「待加强 / 进步」而非「差 / 好」；颜色传达状态不传达情绪。
2. **数据优先可读**：所有数字（得分/计数/掌握度/进度）用等宽 tabular-nums，编码用 mono；一屏看清「现在该干什么」。
3. **精密利落**：10px 卡片圆角、中性低阴影、利落 ease-out；不柔软、不冰凉、不浮夸。
4. **单一强调**：松青 #0f766e 是唯一主色；状态由 accent/warn/danger 承担，无其他装饰色。
5. **零幻觉**：AI 生成内容必须带「模型生成」标注，数字以系统计算为准。
6. **可达性优先**：所有文本 ≥ 4.5:1；焦点可见；动效尊重 reduced-motion；弹窗焦点圈定。

---

## 2. 色彩 Token

| Token | 值 | 用途 |
|---|---|---|
| `--color-canvas` | `#f6f6f4` | 页面暖中性底（去绿调） |
| `--color-surface` | `#ffffff` | 卡片纯白 |
| `--color-surface-2` | `#f1f1ee` | 嵌套次级面（表单内块、hover） |
| `--color-line` | `#e5e5e1` | 中性发丝分割线 |
| `--color-ink` | `#1c1c1a` | 正文墨黑（中性） |
| `--color-ink-soft` | `#575753` | 次级文字 |
| `--color-ink-faint` | `#787872` | 三级文字/编码 **(≥4.5:1)** |
| `--color-accent` | `#0f766e` | 主色·松青（唯一强调） |
| `--color-accent-deep` | `#0c5d56` | 主色按下/深态 |
| `--color-accent-soft` | `#dcefec` | 主色淡底（选中态/高亮块） |
| `--color-warn` | `#b45309` | 警示琥珀 |
| `--color-warn-soft` | `#fbf0df` | 警示淡底 |
| `--color-danger` | `#b3403a` | 危险红 |
| `--color-danger-soft` | `#f7e7e5` | 危险淡底 |

**语义映射**：掌握度高 -> `accent-soft`；中 -> `surface`；低 -> `danger-soft`（柔化，不用纯红冲击）。
**辅色纪律**：无 sage/clay 等装饰辅色；状态仅由 accent/warn/danger 承担。

---

## 3. 圆角（精密，收紧）

| 用途 | 类 | 值 |
|---|---|---|
| 卡片/弹窗 | `rounded-[10px]` | 10px |
| 按钮/输入/下拉 | `rounded-lg` | 8px |
| 徽标/小标签 | `rounded-md` | 6px |
| 进度条/头像/药丸 | `rounded-full` | 9999px |

---

## 4. 阴影（中性、低、利落）

```
--shadow-soft:  0 1px 2px rgba(0,0,0,.04), 0 1px 3px rgba(0,0,0,.06);   /* 卡片静息 */
--shadow-lift:  0 4px 12px -2px rgba(0,0,0,.08);                          /* 卡片悬停 */
--shadow-float: 0 12px 32px -8px rgba(0,0,0,.14);                         /* 弹窗·浮层 */
```
中性灰阴影，不用绿洲的暖绿柔影，保证精密感。

---

## 5. 字体（等宽数字 = 核心特征）

- `--font-sans`: `"Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif`
- `--font-mono`: `"JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace`（知识点编码/得分/数据）
- 正文 14px、行高 1.55；标题 tracking-tight、用 600。
- **所有数据元素强制 `font-variant-numeric: tabular-nums`**（html 已全局默认，数据组件再保险）。
- 不在正文用 <12px；最小 12px（仅元数据/编码可用 12px）。

---

## 6. 动效（利落，非有机慢柔）

| 项 | 绿洲(旧) | 案头(新) |
|---|---|---|
| 缓动 | `[0.22,1,0.36,1]` | `[0.16,1,0.3,1]`（利落 ease-out） |
| nav 激活弹簧 | stiffness 240 / damping 26 | stiffness 320 / damping 30（更干脆） |
| 卡片悬停 | `-translate-y-1` + `shadow-lift` | `-translate-y-0.5` + `shadow-lift`（克制） |
| 路由过渡 | 0.26s | 0.18s（更快） |
| stagger | 0.05s 逐项 | 0.04s；**>16 项降级为整体淡入** |

全部保留 `prefers-reduced-motion` 降级（直接渲染静态内容）。

---

## 7. 组件规范

- **Button**：`rounded-lg px-3.5 py-2 text-sm`；primary 带 `shadow-soft`；active `scale-[0.97]`；disabled 不透明。
- **Card**：`rounded-[10px] border-line bg-surface shadow-soft`；interactive 悬停 `-translate-y-0.5 shadow-lift border-accent/30`。
- **Badge**：`rounded-md px-2 py-0.5`；四语义 tone（neutral/accent/warn/danger）。
- **Input/Select/Textarea**：`rounded-lg`；聚焦细环 `box-shadow: 0 0 0 3px accent/14`。
- **Modal**（统一）：`rounded-[10px] shadow-float`；背景 `bg-ink/40 backdrop-blur-[2px]`；**焦点圈定 + Esc 关闭 + 初始聚焦 + 关闭后归还焦点**。
- **StatTile**（新）：KPI 统计块，大号 tabular-nums 数字 + 标签 + 可选趋势。
- **PipelineCard**（新）：考试阶段卡片，阶段进度点（●◐○）+ 下一动作 CTA。
- **Stepper**（新）：5 阶流水线，`<ol role="list">` + `aria-current`，已完成填实、当前高亮、未达置灰。
- **StatusDot**（新）：●(完成)/◐(进行)/○(未开始) 三态点。
- **TOC**（新）：报告目录，从 markdown 标题提取，sticky。
- **进度条**：`h-1.5 rounded-full`，高态 `bg-accent`、低态 `bg-danger`，利过渡。

---

## 8. 可达性清单（继承绿洲全部修复，不回退）

- [x] `ink-faint` 对比度 ≥4.5:1（#787872）
- [x] 弹窗统一为 `Modal`（焦点圈定 + Esc）-- Collect 手工录入 / Kb 版本切换 / KpDetail 硬删
- [x] 文件上传改为可见按钮 + ref 触发，隐藏 input 用 `sr-only` -- ExamNew / Wizard
- [x] toggle 按钮加 `aria-pressed` -- Quality / Diagnosis「附加 AI 解读」
- [x] 分段控件加 tab 语义 -- ExamNew 拍照/手工
- [x] 表格 `overflow-x-auto` 包裹 -- Exams / Collect
- [x] 诊断/掌握页面包屑
- [x] 硬删 `window.confirm` -> `Modal`
- [x] 11px 字号统一升至 12px
- [x] Kb 列表搜索过滤
- [x] **新增**：Stepper 用 `aria-current="step"`；阶段跳转为真实链接/按钮

---

## 9. 反模式（避免）

- ❌ 引入蓝/紫/粉等非语义色
- ❌ 引入 sage/clay 等装饰辅色（绿洲遗留）
- ❌ 暖绿柔影 / 暖底径向光晕（绿洲遗留）
- ❌ 用 `display:none` 隐藏可聚焦元素（改 `sr-only`）
- ❌ 弹窗裸 `div` 无 role/focus 管理
- ❌ 大列表逐项 stagger（>16 项）
- ❌ emoji 当图标（统一 Phosphor SVG）
- ❌ 11px 及以下正文字号
- ❌ 数据用比例数字（非 tabular-nums）导致跳动
