# 薄弱点分析 · 产品官网

`frontend/site` 是独立的 Vite + React 官网子应用，采用单页结构：产品、方案、安全与部署、
关于四个内容区都在首页纵向展示，顶部不再放分区 tab，页脚保留锚点定位，不切换分区页面。

## 本地开发

```bash
cd frontend/site
npm install
npm run dev       # http://localhost:5174
```

常用锚点：`/#product`、`/#solutions`、`/#security`、`/#about`、`/#cta`。

## 构建与检查

```bash
npm run lint
npm run build
```

## Docker

官网由 `frontend/site/Dockerfile` 构建为 nginx 静态镜像，Compose 默认映射到
`http://localhost:5174`。登录按钮地址由构建参数 `VITE_APP_URL` 注入，应用登录页的“返回官网”
地址由应用构建参数 `VITE_SITE_URL` 注入；两者在正式域名部署时都应覆盖。

历史链接 `/product`、`/solutions`、`/security`、`/about` 保留兼容重定向，会回到首页对应锚点。

视觉规范见 `docs/site-redesign.md`；官网与业务应用共享 C+B 的暖纸、墨青、琥珀和双主题气质，
但各自维护令牌与构建产物。
