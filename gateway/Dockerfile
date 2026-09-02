# sc-gateway 镜像：runtime 魔改壳二进制 + sc 会话网关（FastAPI），自包含。
# 装车批第 5 批：sc MCP python 迁入 backend 容器（streamable-http 挂 /mcp），
# gateway 不再携带 backend python、不再 COPY --from=sc-backend（无构建顺序依赖，
# 镜像瘦身）；容器**不挂 sc-data 卷**——agent 物理不可达 sc.db，直接 DB 读写路径
# 被拓扑切断。镜像内置 runtime Bazel 产物（codex-app-server 直启为网关对接面，
# exec-server 备远程 exec 用）；school-authz-mcp shim 自第 5 批起不再 stage/部署
# （teacher 身份改由 codex MCP client 逐请求 Authorization 头发往 backend /mcp）。
#
# 包结构：gateway 在此目录内为 Python 包（main.py 内 `from gateway.budget import ...`），
# 必须整体拷入 /app/gateway/ 并 `uvicorn gateway.main:app` 启动。
#
# 构建：docker compose -f deploy/docker-compose.yml build gateway
# （先跑 deploy/stage-gateway-runtime.sh 产出 .runtime/ 两二进制——需代理+容器 bazel）

FROM python:3.11-slim
# ca-certificates：壳 TLS（rustls-native-certs）读系统信任库；python3 即本镜像
# 唯一解释器，无 libssl/curl 依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# gateway 自身依赖（自包含，第 5 批起不再复用 backend site-packages）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# runtime 魔改壳二进制（bazel 产物；.runtime/ 由 deploy/stage-gateway-runtime.sh
# 生成，已 gitignore——fresh clone 后首次 build 前先跑该脚本）
COPY .runtime/codex-app-server .runtime/exec-server /usr/local/bin/
RUN chmod +x /usr/local/bin/codex-app-server /usr/local/bin/exec-server \
    && apt-get update \
    && apt-get install -y --no-install-recommends binutils \
    && strip --strip-unneeded /usr/local/bin/codex-app-server /usr/local/bin/exec-server \
    && apt-get purge -y binutils \
    && rm -rf /var/lib/apt/lists/*

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
