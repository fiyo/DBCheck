#!/bin/bash
# ============================================
# DBCheck 启动脚本 (含 RBAC 用户管理模块)
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  DBCheck - 数据库健康巡检平台"
echo "  (RBAC 用户管理模块已集成)"
echo "=========================================="

# 检查 Python 环境
PYTHON="python3"
if ! command -v $PYTHON &> /dev/null; then
    echo "❌ 未找到 Python 3，请先安装 Python 3.9+"
    exit 1
fi

echo "✅ Python: $($PYTHON --version)"

# 安装依赖（如需要）
if [ ! -f ".deps_installed" ]; then
    echo ""
    echo "📦 安装项目依赖..."
    pip3 install -r requirements.txt --break-system-packages 2>/dev/null || \
    pip3 install -r requirements.txt
    touch .deps_installed
    echo "✅ 依赖安装完成"
fi

# 初始化 RBAC 种子数据（如需要）
if [ ! -f "pro_data/.rbac_seeded" ]; then
    echo ""
    echo "🔐 初始化 RBAC 用户管理系统..."
    $PYTHON -m user_management.seed
fi

# 启动应用
echo ""
echo "🚀 启动 DBCheck Web 服务..."
echo "   访问地址: http://localhost:5003"
echo "   管理后台: http://localhost:5003/um/admin"
echo "   登录页面: http://localhost:5003/um/login"
echo ""
echo "   默认管理员: admin / admin123"
echo ""

$PYTHON web_ui.py
