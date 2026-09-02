# sc-gateway 镜像：runtime 魔改壳二进制 + sc 会话网关（FastAPI）+ backend python。
# 装车批第 3 步：node/npm codex 预编译壳退役——本镜像消费 runtime/ Bazel 产物
# （codex-app-server 直启为网关对接面，exec-server 备远程 exec 用，school-authz-mcp
# 作 [mcp_servers.sc] 身份校验 shim）；sc backend python 同镜像收编，使 sc MCP
# stdio 子进程与 codex 同容器（经共享 sc-data 卷读 sc.db）。
#
# 构建顺序：gateway 从 backend 镜像 COPY python 环境与 backend/app（零重复 pip
# install、代码永新鲜）——需先构建 backend：
#   docker compose -f deploy/docker-compose.yml build backend gateway
# 包结构：gateway 在此目录内为 Python 包（main.py 内 `from gateway.budget import ...`），
# 必须整体拷入 /app/gateway/ 并 `uvicorn gateway.main:app` 启动。

ARG SC_BACKEND_IMAGE=sc-backend:latest
FROM ${SC_BACKEND_IMAGE} AS sc-backend-src

FROM python:3.11-slim
# ca-certificates：壳 TLS（rustls-native-certs）读系统信任库；node 退役后 python3
# 即本镜像唯一解释器，无 libssl/curl 依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# backend python 环境 + app 整树（与 backend 同基 python:3.11-slim，cp311 ABI 兼容；
# site-packages 覆盖 gateway 自身 fastapi/uvicorn/pydantic/httpx 依赖）
COPY --from=sc-backend-src /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=sc-backend-src /app/app /app/backend/app
COPY --from=sc-backend-src /app/kb /app/backend/kb

# runtime 魔改壳二进制（bazel 产物；.runtime/ 由 deploy/stage-gateway-runtime.sh
# 生成，已 gitignore——fresh clone 后首次 build 前先跑该脚本）
COPY .runtime/codex-app-server .runtime/exec-server .runtime/school-authz-mcp /usr/local/bin/
RUN chmod +x /usr/local/bin/codex-app-server /usr/local/bin/exec-server /usr/local/bin/school-authz-mcp \
    && apt-get update \
    && apt-get install -y --no-install-recommends binutils \
    && strip --strip-unneeded /usr/local/bin/codex-app-server /usr/local/bin/exec-server /usr/local/bin/school-authz-mcp \
    && apt-get purge -y binutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 包体 + 账号示例；示例兜底为 accounts.json 防启动崩（真实账号由装机渲染覆盖）
COPY main.py __init__.py budget.py dingtalk.py heartbeat.py monthly_usage.py retention.py \
     accounts.example.json codex_home.py /app/gateway/
RUN cp /app/gateway/accounts.example.json /app/gateway/accounts.json
# DeepSeek 接入资产 + 人格 prompt：CODEX_HOME 首启播种（gateway/codex_home.py）消费
COPY assets /app/assets/

ENV SC_GATEWAY_CODEX_HOME=/data/codex-home
ENV SC_GATEWAY_ASSETS=/app/assets/deepseek
VOLUME ["/data"]
EXPOSE 8100
CMD ["python3", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8100"]
