#!/usr/bin/env bash
# Stage runtime 壳二进制到 gateway/.runtime/ 供 gateway 镜像 build（装车批第 3 步）。
#
# runtime/ 的 Bazel 构建走容器（本机无 bazel）：codex-bazel:ready + 缓存卷
# codex-bazel-cache（挂 runtime/:/workspace）。产物在容器内 output base，经第二绑定
# 挂载 gateway/.runtime:/out `cp -L` 取回 host（bazel-bin 在 host 是坏 symlink，不能直接拷）。
#
# 前置：本地代理已开（Bazel 解析阶段需拉 github v8 源码；不开必挂）。
# 用法：  deploy/stage-gateway-runtime.sh
# 之后：  docker compose -f deploy/docker-compose.yml build backend gateway

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$REPO_ROOT/runtime"
OUT_DIR="$REPO_ROOT/gateway/.runtime"
IMAGE="${CODEX_BAZEL_IMAGE:-codex-bazel:ready}"
CACHE_VOL="${CODEX_BAZEL_CACHE:-codex-bazel-cache}"
JOBS="${BAZEL_JOBS:-4}"

# TARGETS:与 runtime/justfile build-for-release 对齐（exec-server 备远程 exec 用）
read -r -d '' BUILD <<EOF || true
set -o pipefail
bazel build --compilation_mode=opt --jobs=${JOBS} \\
  //codex-rs/app-server:codex-app-server \\
  //codex-rs/app-server:exec-server \\
  //codex-rs/school-authz:school-authz-mcp
cp -L bazel-bin/codex-rs/app-server/codex-app-server /out/codex-app-server
cp -L bazel-bin/codex-rs/app-server/exec-server /out/exec-server
cp -L bazel-bin/codex-rs/school-authz/school-authz-mcp /out/school-authz-mcp
chmod +x /out/codex-app-server /out/exec-server /out/school-authz-mcp
EOF

mkdir -p "$OUT_DIR"
# git-bash/WSL 下路径是 POSIX 形式，docker 需要 Windows 形式（D:/sc/...）；
# MSYS_NO_PATHCONV 抑制 MSYS 把容器内 /workspace 这类前导斜杠转成本机路径
export MSYS_NO_PATHCONV=1
if command -v cygpath >/dev/null 2>&1; then
  RUNTIME_MNT="$(cygpath -m "$RUNTIME_DIR")"; OUT_MNT="$(cygpath -m "$OUT_DIR")"
else
  RUNTIME_MNT="$RUNTIME_DIR"; OUT_MNT="$OUT_DIR"
fi
echo "==> bazel build (${IMAGE}, jobs=${JOBS}) → ${OUT_DIR}"
docker run --rm \
  -v "${RUNTIME_MNT}:/workspace" \
  -v "${CACHE_VOL}:/root/.cache" \
  -v "${OUT_MNT}:/out" \
  -w /workspace \
  "${IMAGE}" \
  bash -lc "$BUILD"

echo "==> staged:"
ls -l "$OUT_DIR"
