#!/usr/bin/env bash
# DBCheck Docker 构建脚本
# 用法:
#   ./scripts/build.sh              # 构建基础版（不含 DM8）
#   ./scripts/build.sh --full      # 构建全量版（含 DM8，需要 drivers/dm8/ 下有 dmpython wheel）
#   ./scripts/build.sh --push     # 构建后推送到 Docker Hub
#   ./scripts/build.sh --full --push

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="jackge12345/dbcheck"
VERSION="v2.5.3"

FULL_MODE=0
PUSH_MODE=0

for arg in "$@"; do
    case $arg in
        --full)  FULL_MODE=1 ;;
        --push)  PUSH_MODE=1 ;;
        --help|-h)
            echo "用法: $0 [--full] [--push]"
            echo "  --full   构建全量版（含 DM8，需 drivers/dm8/ 下有驱动）"
            echo "  --push   构建完成后推送到 Docker Hub"
            exit 0
            ;;
    esac
done

cd "$PROJECT_DIR"

echo "==> 检查 Docker..."
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker 未运行，请先启动 Docker Desktop / Docker Engine"
    exit 1
fi

# 检查 DM8 驱动（全量模式）
if [ "$FULL_MODE" = "1" ]; then
    if [ ! -d "drivers/dm8" ] || [ -z "$(ls -A drivers/dm8/*.whl 2>/dev/null || true)" ]; then
        echo "WARNING: --full 模式但需要 drivers/dm8/ 下有 dmpython wheel 文件"
        echo "请将 DM8 Python 驱动（.whl 文件）放到 drivers/dm8/ 目录"
        read -p "是否继续构建（不含 DM8）？[y/N] " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            exit 1
        fi
        FULL_MODE=0
    fi
fi

# 构建参数
BUILD_ARGS=""
TAG_SUFFIX=""
if [ "$FULL_MODE" = "1" ]; then
    BUILD_ARGS="--build-arg WITH_DM=1"
    TAG_SUFFIX="-full"
    echo "==> 构建全量版（含 DM8）..."
else
    echo "==> 构建基础版（不含 DM8）..."
fi

# 构建
docker build \
    $BUILD_ARGS \
    -t "${IMAGE_NAME}:${VERSION}${TAG_SUFFIX}" \
    -t "${IMAGE_NAME}:latest${TAG_SUFFIX}" \
    .

echo ""
echo "✅ 构建完成！"
echo "   镜像: ${IMAGE_NAME}:${VERSION}${TAG_SUFFIX}"
echo "   镜像: ${IMAGE_NAME}:latest${TAG_SUFFIX}"

# 推送
if [ "$PUSH_MODE" = "1" ]; then
    echo ""
    echo "==> 推送到 Docker Hub..."
    docker push "${IMAGE_NAME}:${VERSION}${TAG_SUFFIX}"
    docker push "${IMAGE_NAME}:latest${TAG_SUFFIX}"
    echo "✅ 推送完成！"
fi

echo ""
echo "==> 运行命令："
echo "   docker run -d -p 5000:5000 -v dbcheck_data:/app/data -v dbcheck_reports:/app/reports ${IMAGE_NAME}:latest${TAG_SUFFIX}"
