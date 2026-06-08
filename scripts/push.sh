#!/usr/bin/env bash
# DBCheck Docker 推送脚本
# 用法:
#   ./scripts/push.sh              # 推送基础版
#   ./scripts/push.sh --full      # 同时推送全量版

set -e

IMAGE_NAME="jackge12345/dbcheck"
VERSION="v2.5.3"

PUSH_FULL=0
for arg in "$@"; do
    case $arg in
        --full)  PUSH_FULL=1 ;;
        --help|-h)
            echo "用法: $0 [--full]"
            echo "  --full   同时推送全量版（含 DM8）"
            exit 0
            ;;
    esac
done

echo "==> 检查 Docker 登录状态..."
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker 未运行，请先启动 Docker"
    exit 1
fi

if ! docker system info 2>/dev/null | grep -q "Username"; then
    echo "==> 请先登录 Docker Hub:"
    docker login
fi

echo ""
echo "==> 推送基础版镜像..."
docker push "${IMAGE_NAME}:${VERSION}"
docker push "${IMAGE_NAME}:latest"
echo "✅ 基础版推送完成！"

if [ "$PUSH_FULL" = "1" ]; then
    echo ""
    echo "==> 推送全量版镜像..."
    docker push "${IMAGE_NAME}:${VERSION}-full"
    docker push "${IMAGE_NAME}:latest-full"
    echo "✅ 全量版推送完成！"
fi

echo ""
echo "==> 已完成推送："
echo "   ${IMAGE_NAME}:${VERSION}"
echo "   ${IMAGE_NAME}:latest"
if [ "$PUSH_FULL" = "1" ]; then
    echo "   ${IMAGE_NAME}:${VERSION}-full"
    echo "   ${IMAGE_NAME}:latest-full"
fi
echo ""
echo "用户可通过以下命令拉取："
echo "   docker pull ${IMAGE_NAME}:latest"
