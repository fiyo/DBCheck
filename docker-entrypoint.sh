#!/bin/bash
# DBCheck Docker 启动脚本
# 功能：自动从 GitHub Release 下载 drivers.zip 并解压到 /app/drivers

set -e

APP_DIR="/app"
DRIVERS_DIR="${APP_DIR}/drivers"
GH_REPO="fiyo/DBCheck"
VERSION_FILE="${APP_DIR}/VERSION.txt"

echo "==> DBCheck Docker Entrypoint"

# ── 读取版本号（用于拼 Release 下载地址）─────────────
if [ -f "${VERSION_FILE}" ]; then
    VERSION=$(cat "${VERSION_FILE}" | tr -d '[:space:]')
else
    VERSION="latest"
fi

# ── 下载 drivers.zip（如果不存在）─────────────────────────
if [ ! -d "${DRIVERS_DIR}/oracle_client" ] || [ ! -d "${DRIVERS_DIR}/yashandb" ]; then
    echo "==> drivers/ 目录不完整，尝试从 GitHub Release 下载..."
    DL_URL="https://github.com/${GH_REPO}/releases/download/v${VERSION}/drivers.zip"
    TMP_ZIP="/tmp/drivers.zip"

    echo "    URL: ${DL_URL}"
    curl -fL --retry 3 --retry-delay 5 -o "${TMP_ZIP}" "${DL_URL}" 2>/dev/null && {
        echo "==> 下载成功，解压到 ${DRIVERS_DIR}/"
        mkdir -p "${DRIVERS_DIR}"
        unzip -o "${TMP_ZIP}" -d "${DRIVERS_DIR}/"
        rm -f "${TMP_ZIP}"
        echo "==> drivers/ 准备完成"
    } || {
        echo "WARNING: drivers.zip 下载失败（${DL_URL}），相关数据库类型将不可用。"
        echo "   可手动将驱动放到挂载卷的 /app/drivers/ 目录"
    }
else
    echo "==> drivers/ 目录已存在，跳过下载"
fi

# ── 启动应用 ─────────────────────────────────────────────
echo "==> 启动 DBCheck Web UI..."
cd "${APP_DIR}"
exec python web_ui.py "$@"
