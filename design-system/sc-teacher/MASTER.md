# 薄弱点分析·教师工作台 — 设计系统源文件（绿洲 / Biophilic Calm）

> 全站唯一视觉真相来源。所有页面、组件、动效必须遵循本文件。
> 方向：在既有「暖纸底 + 墨绿成长框架」基础上，向**有机、柔和、成长感**推进——
> 更大的圆角、暖色柔和阴影、森林绿 + 暖沙辅色、更柔的有机动效。
> 不引入 AI 紫蓝；保持克制；教师拥有最终否决权。

---

## 1. 设计原则

1. **成长框架，非评判**：用「待加强 / 进步」而非「差 / 好」；颜色传达状态不传达情绪。
2. **纸本可读**：暖纸底、高对比墨字、报告页像排版精良的期刊；数据可被打印。
3. **有机柔和**：16–20px 圆角、暖色柔影、自然 ease-out；不锐利、不冰凉。
4. **单一强调**：墨绿是唯一主色；暖沙/clay 仅作温度辅色，克制使用。
5. **零幻觉**：AI 生成内容必须带「模型生成」标注，数字以系统计算为准。
6. **可达性优先**：所有文本 ≥ 4.5:1；焦点可见；动效尊重 reduced-motion；弹窗焦点圈定。

---

## 2. 色彩 Token

| Token | 值 | 用途 |
|---|---|---|
| `--color-paper` | `#f4f2ea` | 页面暖纸底（比旧 #f6f7f3 更暖） |
| `--color-surface` | `#fffdf8` | 卡片暖白 |
| `--color-surface-2` | `#faf7ee` | 嵌套次级面（表单内块、hover） |
| `--color-line` | `#e3ddce` | 暖色发丝分割线 |
| `--color-ink` | `#1f261e` | 正文墨黑（偏绿暖） |
| `--color-ink-soft` | `#515c4a` | 次级文字（暖沙绿灰） |
| `--color-ink-faint` | `#67725a` | 三级文字/编码 **(已修对比度 ≥4.5:1)** |
| `--color-accent` | `#2f6b4f` | 主色·森林墨绿（保持不变） |
| `--color-accent-deep` | `#215039` | 主色按下/深态 |
| `--color-accent-soft` | `#e6efe4` | 主色淡底（选中态/高亮块） |
| `--color-sage` | `#8fa896` | **新** 柔和鼠尾草绿（插画/次级面） |
| `--color-sage-soft` | `#eef2ea` | **新** 鼠尾草淡底 |
| `--color-clay` | `#bd7a52` | **新** 暖陶土/沙色辅色（温度点缀，克制） |
| `--color-clay-soft` | `#f3e4d6` | **新** 陶土淡底 |
| `--color-warn` | `#b45309` | 警示琥珀 |
| `--color-warn-soft` | `#fbf0df` | 警示淡底 |
| `--color-danger` | `#b3403a` | 危险红 |
| `--color-danger-soft` | `#f7e7e5` | 危险淡底 |

**语义映射**：掌握度高 → `accent-soft`/`sage-soft`；中 → `surface`；低 → `danger-soft`（柔化，不用纯红冲击）。
**辅色纪律**：`clay` 仅用于温度点缀（空状态插画、次级强调），不用于状态判定；状态仍由 accent/warn/danger 承担。

---

## 3. 圆角（有机，加大）

| 用途 | 类 | 值 |
|---|---|---|
| 卡片/弹窗 | `rounded-2xl` | 16px |
| 按钮/输入/下拉 | `rounded-xl` | 12px |
| 徽标/小标签 | `rounded-lg` | 8px |
| 进度条/头像/药丸 | `rounded-full` | 9999px |

---

## 4. 阴影（暖色柔和，非纯黑）

```
--shadow-soft:  0 2px 8px -2px rgba(31,38,30,0.08);   /* 卡片静息 */
--shadow-lift:  0 10px 28px -10px rgba(31,38,30,0.14); /* 卡片悬停·轻浮 */
--shadow-float: 0 18px 44px -14px rgba(31,38,30,0.18); /* 弹窗·浮层 */
```
阴影色调取自 `ink`（偏绿暖），不用纯黑 `rgba(0,0,0)`，保证暖度统一。

---

## 5. 字体

- `--font-sans`: `"Outfit", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif`
- `--font-mono`: `"JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace`（知识点编码/得分/数据）
- 正文 15px、行高 1.6；标题 tracking-tight、用 600 而非 700（更柔和）。
- 不在正文用 <12px；最小 12px（仅元数据/编码可用 12px）。

---

## 6. 动效（有机，更柔）

| 项 | 旧 | 新 |
|---|---|---|
| 缓动 | `[0.16,1,0.3,1]` | `[0.22,1,0.36,1]`（更有机 ease-out） |
| nav 激活弹簧 | stiffness 300 / damping 30 | stiffness 240 / damping 26（更柔） |
| 卡片悬停 | `-translate-y-0.5` + `shadow-md` | `-translate-y-1` + `shadow-lift`（更明显浮起） |
| 路由过渡 | 0.22s | 0.26s（更从容） |
| stagger | 0.06s 逐项 | 0.05s；**>16 项降级为整体淡入**（避免大列表 2s+ 迟滞） |

全部保留 `prefers-reduced-motion` 降级（直接渲染静态内容）。

---

## 7. 组件规范

- **Button**：`rounded-xl px-4 py-2.5`；primary 带 `shadow-soft`；active `scale-[0.97]`；disabled 不透明。
- **Card**：`rounded-2xl border-line bg-surface shadow-soft`；interactive 悬停 `-translate-y-1 shadow-lift border-accent/30`。
- **Badge**：`rounded-lg px-2.5 py-0.5`；四语义 tone（neutral/accent/warn/danger）。
- **Input/Select/Textarea**：`rounded-xl`；聚焦用柔光环 `box-shadow: 0 0 0 3px accent/15`（非纯边框）。
- **Modal**（新统一）：`rounded-2xl shadow-float`；背景 `bg-ink/30 backdrop-blur-[2px]`；**焦点圈定 + Esc 关闭 + 初始聚焦 + 关闭后归还焦点**。
- **SectionTitle**（新）：小圆点（sage）+ 标题，用于区段标题，替代裸 `<h2>`。
- **EmptyState**：图标置于 `sage-soft` 圆形底内，暖化空状态。
- **进度条**：`h-2 rounded-full`，高态 `bg-accent`、低态 `bg-danger`，柔过渡。

---

## 8. 可达性清单（重设计同步修复）

- [x] P0-1 `ink-faint` 加深至 `#67725a`（≥4.5:1）
- [x] P0-2 弹窗统一为 `Modal`（焦点圈定 + Esc）—— Collect 手工录入 / Kb 版本切换 / KpDetail 硬删
- [x] P0-3 文件上传改为可见按钮 + ref 触发，隐藏 input 用 `sr-only`（键盘可达）—— ExamNew / Wizard
- [x] P1-1 toggle 按钮加 `aria-pressed` —— Quality / Diagnosis「附加 AI 解读」
- [x] P1-2 分段控件加 tab 语义 —— ExamNew 拍照/手工
- [x] P1-3 表格 `overflow-x-auto` 包裹 —— Exams / Collect
- [x] P1-4 诊断/掌握页加「返回学生列表」面包屑
- [x] P2-3 硬删 `window.confirm` → `Modal`
- [x] P2-4 11px 字号统一升至 12px
- [x] P2-5 Kb 列表加搜索过滤

---

## 9. 反模式（避免）

- ❌ 引入蓝/紫/粉等非语义色
- ❌ 用 `display:none` 隐藏可聚焦元素（改 `sr-only`）
- ❌ 弹窗裸 `div` 无 role/focus 管理
- ❌ 大列表逐项 stagger（>16 项）
- ❌ 纯黑阴影 / 纯红冲击色
- ❌ emoji 当图标（统一 Phosphor SVG）
- ❌ 11px 及以下正文字号
