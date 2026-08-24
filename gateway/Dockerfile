# gateway 镜像：codex 壳（npm 预编译二进制）+ sc 会话网关（FastAPI）。
# Phase 1 形态：壳用官方预编译（与锚点同版本）；裁剪完成后切换为 runtime/ 源码构建
# 的魔改壳（届时替换本文件的安装段）。
FROM node:22-slim

ARG CODEX_NPM_VERSION=0.149.1
ARG TARGETARCH=arm64

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.11-minimal python3-pip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    || (apt-get update && apt-get install -y --no-install-recommends python3 python3-pip curl ca-certificates && rm -rf /var/lib/apt/lists/*)

# codex CLI：按架构选官方预编译发行包（app-server 二进制随附）
RUN codex_pkg=""; \
    case "$TARGETARCH" in \
      arm64) codex_pkg="@openai/codex-darwin-arm64" ;; \
      amd64) codex_pkg="@openai/codex-linux-x64" ;; \
      *) echo "unsupported arch: $TARGETARCH" && exit 1 ;; \
    esac; \
    npm install -g --omit=dev "@openai/codex@${CODEX_NPM_VERSION}" "codex-npm-placeholder" 2>/dev/null; \
    npm install -g --omit=dev "@openai/codex@${CODEX_NPM_VERSION}"; \
    codex --version

RUN pip3 install --no-cache-dir --break-system-packages \
      "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.7"

WORKDIR /app
COPY main.py __init__.py accounts.example.json /app/
# DeepSeek 接入资产 + 人格 prompt：装机向导渲染到 CODEX_HOME
COPY assets /app/assets/

ENV SC_GATEWAY_CODEX_HOME=/data/codex-home
VOLUME ["/data"]
EXPOSE 8100
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
