---
name: zhixing-startup
description: Start, verify, restart, or stop the current 知行教研 project across Docker Compose and local development on macOS, Windows, and Linux. Use when the user asks to run the project, bring up an endpoint, restart services, or diagnose startup failures.
---

# 知行教研项目启动

这是当前仓库专属的启动技能。Codex 和 Claude 都应按本文件操作，不要把它当成通用的 React、FastAPI 或 Docker 教程。

## 固定规则

1. 先确认当前目录是仓库根目录：必须能看到 deploy/docker-compose.yml、backend/、frontend/ 和 gateway/。
2. 默认复用现有 Compose 项目名 deploy：所有 Compose 命令都带 -p deploy，避免因为工作目录不同而创建第二套容器。
3. 启动前确认 backend/.env 存在；不存在时从 backend/.env.example 复制。不要输出、提交或覆盖其中的密钥。
4. 不使用 docker compose down -v，不删除 sc-data、sc-backups 或 gw-codex-home 卷，除非用户明确要求清空数据。
5. 不直接运行 runtime/ 里的源码来代替 gateway。Compose gateway 依赖 gateway/.runtime/codex-app-server 和 gateway/.runtime/exec-server。
6. 启动后必须检查 docker compose ... ps 和健康端点；只看到容器 Up 不等于服务已经 ready。

## 选择启动 profile

### Profile A：完整 Docker Compose（默认、跨平台推荐）

用于验收完整产品、AI 教研员、SSE/RPC、备份和 LLM 路由。需要 macOS/Windows 的 Docker Desktop，或 Linux 的 Docker Engine + Compose v2。

    入口              地址
    官网              http://127.0.0.1:5174
    教师端            http://127.0.0.1:8080
    后端直连          http://127.0.0.1:8000
    网关直连          http://127.0.0.1:8100
    LLM 路由          http://127.0.0.1:8090

浏览器只使用官网 5174 或教师端 8080。浏览器侧的 /api、/rpc 和 /threads/* 由教师端 nginx 反代到 Compose 内的 backend:8000 和 gateway:8100，不要在浏览器里把 gateway:8100 当作 SSE 入口。

### Profile B：本地开发（不启动 gateway）

用于前端/后端日常开发：官网 Vite 5174、教师端 Vite 5173、FastAPI 8000。教师端 Vite 已把 /api 代理到 127.0.0.1:8000，把 /rpc 和 /threads 代理到 127.0.0.1:8100。

没有 gateway 时普通数据页面仍可用，但 AI 教研员和会话功能不可用；不要把这两个端点的失败误报成前端启动失败。需要完整 AI 会话时使用 Profile A。

### Profile C：HA/多实例（仅用户明确要求时）

这是 PostgreSQL + Redis + MinIO 的实验性扩展，不是默认启动方式：

    docker compose -p deploy -f deploy/docker-compose.yml -f deploy/docker-compose.ha.yml up -d --build --scale backend=2

不要为了普通本地启动自动启用该 profile。gateway 仍保持单副本，数据卷和配置也与单机 profile 不同。

## 完整 Docker Compose 启动

### 首次 clone 或 gateway runtime 缺失

检查以下两个文件：

    gateway/.runtime/codex-app-server
    gateway/.runtime/exec-server

缺失时先准备 codex-bazel:ready 镜像和 Docker 缓存卷，再执行 runtime staging：

- macOS/Linux：在仓库根目录执行 bash deploy/stage-gateway-runtime.sh。
- Windows：使用 WSL2 或 Git Bash 执行同一脚本，并确保 Docker Desktop 使用 Linux containers；不要在原生 PowerShell 中把 .sh 当作 PowerShell 脚本执行。

该脚本会在容器内编译 Linux gateway runtime，再把产物放回 gateway/.runtime/。macOS 和 Windows 不应尝试直接在宿主机执行这些 Linux 二进制；让 gateway 留在 Linux 容器里运行。网络受限时先配置 Docker/系统代理。

如果 codex-bazel:ready 不存在，不要伪造空文件绕过构建；先查看 runtime/ 的 Bazel 构建说明或让用户准备该镜像。

### 准备配置并启动

macOS/Linux：

    test -f backend/.env || cp backend/.env.example backend/.env
    docker compose -p deploy -f deploy/docker-compose.yml config --quiet
    docker compose -p deploy -f deploy/docker-compose.yml up -d --build

Windows PowerShell：

    if (-not (Test-Path -LiteralPath 'backend\.env')) { Copy-Item 'backend\.env.example' 'backend\.env' }
    docker compose -p deploy -f deploy\docker-compose.yml config --quiet
    docker compose -p deploy -f deploy\docker-compose.yml up -d --build

启动后检查：

    docker compose -p deploy -f deploy/docker-compose.yml ps

只改官网、教师端或后端时，只重建对应服务：

    docker compose -p deploy -f deploy/docker-compose.yml up -d --no-deps --build site
    docker compose -p deploy -f deploy/docker-compose.yml up -d --no-deps --build frontend
    docker compose -p deploy -f deploy/docker-compose.yml up -d --no-deps --build backend

### Compose 端点与健康检查

    服务          宿主端口  健康检查                 说明
    site          5174      /healthz                 静态官网
    frontend      8080      /healthz、/ready         浏览器入口，反代 /api、/rpc、/threads
    backend       8000      /health、/ready、/docs   仅本机调试
    gateway       8100      /health                  AI 会话网关
    llm-router    8090      /health、/metrics        LLM 内部路由

macOS/Linux 使用 curl -fsS http://127.0.0.1:PORT/PATH；Windows PowerShell 使用 Invoke-WebRequest -UseBasicParsing URL。至少检查 5174/healthz、8080/healthz、8080/ready、8000/health、8000/ready、8100/health 和 8090/health。

失败时按依赖顺序查看日志：

    docker compose -p deploy -f deploy/docker-compose.yml logs --tail=120 llm-router backend gateway frontend site

## 本地开发启动

本地开发需要 Python 3.11+、Node.js 20+、npm。没有模型密钥时，确定性分析和基础数据功能仍可启动，模型相关请求会降级或 fail-closed。

### macOS/Linux

    test -f backend/.env || cp backend/.env.example backend/.env
    python3 -m venv backend/.venv
    backend/.venv/bin/python -m pip install -r backend/requirements.txt

分别打开三个终端：

    # 后端
    backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000

    # 教师端
    cd frontend/app && npm ci && npm run dev -- --host 127.0.0.1

    # 官网
    cd frontend/site && npm ci && npm run dev -- --host 127.0.0.1

### Windows PowerShell

    if (-not (Test-Path -LiteralPath 'backend\.env')) { Copy-Item 'backend\.env.example' 'backend\.env' }
    py -3.11 -m venv 'backend\.venv'
    & 'backend\.venv\Scripts\python.exe' -m pip install -r 'backend\requirements.txt'

分别打开三个 PowerShell：

    # 后端
    & 'backend\.venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000

    # 教师端
    Set-Location 'frontend\app'; npm ci; npm run dev -- --host 127.0.0.1

    # 官网
    Set-Location 'frontend\site'; npm ci; npm run dev -- --host 127.0.0.1

### 本地 LLM router（可选）

只有需要单独调试路由器时才启动它。宿主端点是 http://127.0.0.1:8090/v1；Compose 内部必须使用 http://llm-router:8090/v1，不能把 127.0.0.1 写进容器间配置。

    cd llm_router
    python3 -m pip install -r requirements.txt
    python3 -m uvicorn main:app --host 127.0.0.1 --port 8090

Windows 将 python3 替换为 py -3.11 或当前虚拟环境解释器。设置 SC_LLM_ROUTER_URL=http://127.0.0.1:8090/v1 后重启本地 backend。路由器没有 provider key 时会健康但对真实模型请求 fail-closed，这是预期行为。

## 不同宿主端口和外部端点

### 宿主 8000 被占用

    docker compose -p deploy -f deploy/docker-compose.yml -f deploy/compose.local-override.yml up -d --build

此时 backend 宿主调试端点变为 127.0.0.1:18000，但 Compose 网络中的 backend:8000 不变；官网 5174、教师端 8080、gateway 8100 不变。本地 Vite 配置固定代理到 8000，不要把它和 18000 混用。

### 部署到非本机域名

构建前设置 VITE_APP_URL=https://app.example.com 和 VITE_SITE_URL=https://www.example.com；backend 的 SC_CORS_ORIGINS 填真实浏览器来源。容器内部仍使用 SC_LLM_ROUTER_URL=http://llm-router:8090/v1 和 SC_GATEWAY_URL=http://gateway:8100。不要把 provider API key 写入前端变量、Dockerfile、命令行历史或 skill 文档。

## 停止、重启和故障定位

    # 保留数据卷，只停止服务
    docker compose -p deploy -f deploy/docker-compose.yml stop

    # 保留卷，移除容器和网络；下次仍用同一项目名重建
    docker compose -p deploy -f deploy/docker-compose.yml down

    # 只重启一个服务
    docker compose -p deploy -f deploy/docker-compose.yml restart backend

除非用户明确要求数据清理，否则禁止 down -v、删除 Compose 卷、删除 sc.db 或删除 gateway/.runtime。

标准排查顺序：

1. docker compose -p deploy ... ps：确认服务是否存在、是否 healthy。
2. 先看依赖：llm-router → backend → gateway → frontend。
3. 对照端点矩阵区分 127.0.0.1（宿主）和 backend/gateway/llm-router（Compose 网络）。
4. 检查 .env 是否只是缺配置，不要通过复制密钥到命令行临时修复。
5. gateway 构建失败时，优先确认两个 .runtime 文件和 Docker Linux 容器架构，不要修改 nginx 来掩盖 runtime 缺失。

## Codex 与 Claude 的执行约定

- Codex 使用 .agents/skills/zhixing-startup/SKILL.md；Claude 使用 .claude/skills/zhixing-startup/SKILL.md。两份内容必须保持一致。
- 两个客户端都应先判断操作系统、Docker/Compose 是否可用，再选择 Profile A 或 B；不要默认假设 Bash、PowerShell 或 Docker 已安装。
- 用户只说“启动项目”时默认选择完整 Docker Compose；如果 gateway runtime 缺失，先报告前置条件，并说明可先启动本地开发 Profile B。
- 用户指定某个端点时，只启动满足该端点的最小 profile；但如果端点依赖 gateway 或 LLM router，要把依赖一起说明。
- 启动完成后报告实际访问 URL、健康检查结果、使用的 profile，以及未启动的可选端点。不要声称 AI 会话可用，除非 gateway /health 和教师端 /ready 都通过。
