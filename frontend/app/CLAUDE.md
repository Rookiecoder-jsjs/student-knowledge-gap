# frontend/app 前端约定

- 新增页面/组件必须遵循设计风格规范：`docs/design-style.md`（令牌/布局/组件/动效/双主题规则与页面模板）；决策背景见 `docs/saas-redesign.md`。
- 路由纪律：路由元素根类型必须是 `<Shell>` 本体（瞬跳教训）；页面包 `<Animated>`；`<Guard>` 在 Shell 内层。
- 门禁：`npm run build`（tsc -b && vite build）与 `npm run lint`（oxlint）全绿方可交付验证；本地验证走 docker frontend 容器重建 + 浏览器硬刷。
