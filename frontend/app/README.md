# 薄弱点分析 · 教师工作台

`frontend/app` 是项目的业务前端，面向教师、管理员与学生三类账号。它与官网
`frontend/site` 分开构建：应用使用相对 `/api`、`/rpc`、`/threads` 请求，由 nginx
统一反向代理到后端与会话网关。

## 本地开发

```bash
cd frontend/app
npm install
npm run dev       # http://localhost:5173
```

Vite 开发代理默认指向：

- `/api` → `http://127.0.0.1:8000`（FastAPI）
- `/rpc`、`/threads` → `http://127.0.0.1:8100`（会话网关）

## 生产构建

```bash
npm run lint      # oxlint
npm run build     # tsc -b && vite build
```

Docker 部署时由 `deploy/docker-compose.yml` 构建 nginx 镜像并暴露在
`http://localhost:8080`。登录页返回官网的地址由构建参数 `VITE_SITE_URL` 注入，默认
为 `http://127.0.0.1:5174`；正式环境请在构建前覆盖该变量。

## 页面结构

- 教师端：班级概览、考试五阶流水线、质量报告、学生诊断、知识库、待签发与 AI 教研员
- 管理端：账号、知识库与用量管理（按角色守卫）
- 学生端：薄弱点、掌握度、报告、学习方案
- 全局：暖纸浅色 / 低眩光暗色双主题，交互控件提供悬停与按下反馈

新增页面和组件请遵循仓库内 `docs/design-style.md` 以及 `frontend/app/CLAUDE.md` 中的
令牌、路由、动效和响应式约定。

## AI 教研员会话

`/assistant` 采用桌面 Agent 的会话布局：左侧历史会话、中央流式对话、超宽屏会话上下文栏；窄屏以抽屉承载历史与上下文。当前已接入：

- `thread/list` / `thread/resume` / `thread/archive` 会话生命周期；
- `model/list` 与思考强度选择，按会话生效并记忆上次选择；
- 快捷教学问题、流式回答、工具调用状态、回答复制；
- 当前班级范围、只读边界、待签发/知识库/班级工作台的上下文入口。

页面刻意将 MCP 工具名转换为教师可读的业务动作，当前由 backend `/mcp` 注册
10 个工具（8 个只读、2 个写入）；写入工具的实际注册名带 `_tool` 后缀，后续可沿
现有会话边界扩展：

1. 考后自动分析任务卡片，点击后进入对应持久班级线程；
2. 回答中的 `_provenance` 依据链接，展开原始考试/掌握度数据；
3. `create_report_draft_tool`、`record_intervention_tool` 草稿预览与待签发确认
   （实现函数分别为 `create_report_draft`、`record_intervention`）；
4. 班级长期记忆、按考试/知识点筛选历史，以及会话摘要与用量提示；
5. 在不改变 SSE/RPC 协议的前提下增加附件、模板化提问和教研结果导出。
