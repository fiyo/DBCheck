#!/usr/bin/env bash
#
# DBCheck 多架构（amd64 + arm64）Docker 构建脚本
#
# 用法:
#   ./scripts/build-multiarch.sh                 # 仅构建并加载当前主机架构镜像（本地测试）
#   ./scripts/build-multiarch.sh --push         # 构建 amd64+arm64 并推送到 GHCR + Docker Hub
#   ./scripts/build-multiarch.sh --full --push  # 全量版（含 DM8）
#
# 前置条件:
#   - Docker >= 20.10（支持 buildx）
#   - 多架构推送需要 buildx + QEMU（脚本会自动尝试注册 QEMU）
#
# 为什么需要多架构？
#   openEuler / 信创服务器多为 ARM64（鲲鹏、飞腾）。旧镜像只有 amd64，
#   在 ARM64 上靠 qemu 仿真运行，导致 numpy 2.x 报 "X86_V2 unsupported" 崩溃。
#   构建原生 arm64 镜像后，numpy / OpenJDK 原生运行，问题消失。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

DHUB_IMAGE="jackge12345/dbcheck"
GHCR_IMAGE="ghcr.io/fiyo/dbcheck"

APP_PORT="5003"
PLATFORMS="linux/amd64,linux/arm64"

FULL_MODE=0
PUSH_MODE=0

for arg in "$@"; do
    case $arg in
        --full)  FULL_MODE=1 ;;
        --push)  PUSH_MODE=1 ;;
        --help|-h)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "未知参数: $arg (使用 --help 查看用法)"
            exit 1
            ;;
    esac
done

cd "$PROJECT_DIR"

# ── 版本号（取自 modules/config/version.py，避免硬编码漂移）──────────────
VERSION="$(grep -m1 '__version__' "$PROJECT_DIR/modules/config/version.py" 2>/dev/null \
            | sed -E "s/.*=[[:space:]]*['\"]//; s/['\"].*//")"
if [ -z "$VERSION" ]; then
    VERSION="v26.9.9.0"
    echo "WARNING: 未能从 version.py 解析版本号，回退到 $VERSION"
fi

# ── 校验环境 ─────────────────────────────────────────────────────────────
echo "==> 检查 Docker / buildx..."
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker 未运行，请先启动 Docker Engine / Desktop"
    exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
    echo "ERROR: 未找到 docker buildx，请升级 Docker 到 20.10+"
    exit 1
fi

# ── 全量模式（DM8 驱动）─────────────────────────────────────────────────
BUILD_ARGS=""
TAG_SUFFIX=""
if [ "$FULL_MODE" = "1" ]; then
    BUILD_ARGS="--build-arg WITH_DM=1"
    TAG_SUFFIX="-full"
    echo "==> 全量模式（含 DM8，需 drivers/dm8/ 下有 dmpython wheel）"
    if [ ! -d "drivers/dm8" ] || [ -z "$(ls -A drivers/dm8/*.whl 2>/dev/null || true)" ]; then
        echo "ERROR: --full 需要 drivers/dm8/ 下的 dmpython wheel，请先准备"
        exit 1
    fi
else
    echo "==> 基础模式（不含 DM8）"
fi

# 始终透传版本号给 Dockerfile（避免镜像内 VERSION.txt 与 tag 不一致）
BUILD_ARGS="$BUILD_ARGS --build-arg DBCHECK_VERSION=$VERSION"

# ── 组装 tags ─────────────────────────────────────────────────────────────
TAGS=""
add_tag() { TAGS="$TAGS --tag $1"; }
add_tag "${DHUB_IMAGE}:${VERSION}${TAG_SUFFIX}"
add_tag "${DHUB_IMAGE}:latest${TAG_SUFFIX}"
add_tag "${GHCR_IMAGE}:${VERSION}${TAG_SUFFIX}"
add_tag "${GHCR_IMAGE}:latest${TAG_SUFFIX}"

# ── 多架构 builder + QEMU 注册 ──────────────────────────────────────────
echo "==> 准备 buildx builder（含 QEMU 仿真，支持跨架构构建）..."
docker buildx create --name dbcheck-builder --driver docker-container --use 2>/dev/null \
    || docker buildx use dbcheck-builder 2>/dev/null \
    || true
# 注册 QEMU（使得可在 x86 主机上构建 arm64，反之亦然）
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes 2>/dev/null || \
    echo "WARNING: QEMU 注册失败（若宿主机原生支持目标架构可忽略）"

# ── 决定 push / load ─────────────────────────────────────────────────────
if [ "$PUSH_MODE" = "1" ]; then
    PUSH_FLAG="--push"
    echo "==> 多架构构建并推送: $PLATFORMS"
else
    # 多架构无法本地 --load，退化为仅构建当前主机架构用于本地测试
    NATIVE="$(uname -m)"
    case "$NATIVE" in
        x86_64)         PLATFORMS="linux/amd64" ;;
        aarch64|arm64)  PLATFORMS="linux/arm64" ;;
        *)              PLATFORMS="linux/amd64" ;;
    esac
    PUSH_FLAG="--load"
    echo "==> 本地单架构构建（--push 才能产出真正的多架构镜像）: $PLATFORMS"
fi

# ── 执行构建 ─────────────────────────────────────────────────────────────
# shellcheck disable=SC2086
docker buildx build -f deploy/Dockerfile \
    --platform "$PLATFORMS" \
    $BUILD_ARGS \
    $TAGS \
    $PUSH_FLAG \
    .

echo ""
echo "✅ 构建完成！"
if [ "$PUSH_MODE" = "1" ]; then
    echo "   已推送多架构镜像 (${PLATFORMS}) 到:"
    echo "     ${GHCR_IMAGE}:${VERSION}${TAG_SUFFIX}"
    echo "     ${GHCR_IMAGE}:latest${TAG_SUFFIX}"
    echo "     ${DHUB_IMAGE}:${VERSION}${TAG_SUFFIX}"
    echo "     ${DHUB_IMAGE}:latest${TAG_SUFFIX}"
else
    echo "   本地镜像: ${DHUB_IMAGE}:latest${TAG_SUFFIX} (仅 ${PLATFORMS})"
    echo "   运行: docker run -d -p ${APP_PORT}:${APP_PORT} -v dbcheck_data:/app/data -v dbcheck_reports:/app/data/reports ${DHUB_IMAGE}:latest${TAG_SUFFIX}"
fi
