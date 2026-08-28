# gateway 镜像：codex 壳（npm 预编译二进制）+ sc 会话网关（FastAPI）。
# Phase 1 形态：壳用官方预编译（与锚点同版本）；裁剪完成后切换为 runtime/ 源码构建
# 的魔改壳（届时替换本文件的安装段）。
#
# 包结构：gateway 在此目录内为 Python 包（main.py 内 `from gateway.budget import ...`），
# 必须整体拷入 /app/gateway/ 并 `uvicorn gateway.main:app` 启动——拍平拷文件会
# ModuleNotFoundError（此前 CI 只构建不运行，此运行时 break 未被捕获）。
FROM node:22-slim

ARG CODEX_NPM_VERSION=0.149.1
ARG TARGETARCH=arm64

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.11-minimal python3-pip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    || (apt-get update && apt-get install -y --no-install-recommends python3 python3-pip curl ca-certificates && rm -rf /var/lib/apt/lists/*)

# codex CLI：官方预编译发行包（@openai/codex 主包按平台可选依赖拉二进制，app-server 随附）
RUN codex_pkg=""; \
    case "$TARGETARCH" in \
      arm64) codex_pkg="@openai/codex-darwin-arm64" ;; \
      amd64) codex_pkg="@openai/codex-linux-x64" ;; \
      *) echo "unsupported arch: $TARGETARCH" && exit 1 ;; \
    esac; \
    npm install -g --omit=dev "@openai/codex@${CODEX_NPM_VERSION}" "codex-npm-placeholder" 2>/dev/null; \
    npm install -g --omit=dev "@openai/codex@${CODEX_NPM_VERSION}"; \
    codex --version

# 依赖以 requirements.txt 为准（含 httpx——月度软限额巡查 §5.7 调用 backend）
COPY requirements.txt /tmp/gateway/
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/gateway/requirements.txt

WORKDIR /app
# 包体 + 账号示例；示例兜底为 accounts.json 防启动崩（真实账号由装机向导渲染覆盖）
COPY main.py __init__.py budget.py dingtalk.py heartbeat.py monthly_usage.py retention.py accounts.example.json /app/gateway/
RUN cp /app/gateway/accounts.example.json /app/gateway/accounts.json
# DeepSeek 接入资产 + 人格 prompt：装机向导渲染到 CODEX_HOME
COPY assets /app/assets/

ENV SC_GATEWAY_CODEX_HOME=/data/codex-home
VOLUME ["/data"]
EXPOSE 8100
CMD ["python3", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8100"]