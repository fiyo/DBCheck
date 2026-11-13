#!/bin/bash
# ============================================================
# RaccoonX Linux Build Script
# Target: glibc >= 2.28 的 x86_64 / aarch64 发行版
#   （RHEL 8 / AlmaLinux 8 / 银河麒麟 V10 SP3 / 统信 UOS / Ubuntu 20.04+ 等）
#   CI 在 quay.io/pypa/manylinux_2_28_{x86_64,aarch64} 容器内构建，
#   产物即以此为最低 glibc 基线；压缩包名按构建容器的 uname -m 自动带架构后缀。
# Requires: Python >= 3.10, pip, gcc
# ============================================================
set -e

echo "========================================"
echo "  RaccoonX Linux Build Script"
echo "========================================"
echo ""

# Change to project root
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
echo "[1/5] Project root: $PROJECT_ROOT"

# Check Python version
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v $cmd &> /dev/null; then
        PYTHON_CMD=$cmd
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "[ERROR] Python not found. Please install Python >= 3.10."
    echo "  CentOS: sudo yum install python3"
    exit 1
fi

PYTHON_VER=$($PYTHON_CMD -c "import sys; v=sys.version_info; print(str(v[0])+'.'+str(v[1]))")
echo "[1/5] Python version: $PYTHON_VER"

MAJOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.major)")
MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)")

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
    echo "[ERROR] Python >= 3.10 required (current: $PYTHON_VER)"
    echo "  请在目标发行版上安装 Python 3.10+（含 -devel / venv 组件），例如："
    echo "    RHEL 8 / 麒麟 V10 SP3: sudo dnf install -y python3.11 python3.11-devel"
    echo "    Debian / Ubuntu:      sudo apt install -y python3.11 python3.11-venv python3.11-dev"
    exit 1
fi

echo "[2/5] Checking dependencies..."

# Create virtual environment
VENV_DIR="$PROJECT_ROOT/.venv_build"
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    $PYTHON_CMD -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# 升级 pip/setuptools/wheel：提高 manylinux_2_28 预编译 wheel 命中率，
# 避免 JPype1/Pillow 等被迫从源码编译（缺 jpeg 等系统库）
# --timeout/--retries 防御 CI 网络抖动导致大 wheel 下载超时 fallback 源码编译
pip install --upgrade pip setuptools wheel --timeout 120 --retries 10

# Check pyinstaller
if ! command -v pyinstaller &> /dev/null; then
    echo "  Installing pyinstaller..."
    pip install pyinstaller --quiet
fi

echo "[3/5] Installing project dependencies..."
# CI 宽松模式（DBCHECK_CI_LENIENT=1）：逐个依赖安装，装不上的可选驱动跳过，
# 最后统一 import 校验构建期必需模块。未设该变量时行为与原先完全一致。
if [ "${DBCHECK_CI_LENIENT:-0}" = "1" ]; then
    "$PYTHON_CMD" build/ci_install_deps.py
else
    pip install -r deploy/requirements.txt --quiet
fi

echo "[4/5] Building executable..."
# Clean old build artifacts (NOT the build/ directory)
rm -rf dist __pycache__ build_pyinstaller_tmp
$PYTHON_CMD -m PyInstaller build/dbcheck_linux.spec --noconfirm

echo "[5/5] Packaging release..."

# Create start script
BUILDDIR="dist/RaccoonX-Linux"
cat > "$BUILDDIR/start.sh" << 'STARTEOF'
#!/bin/bash
cd "$(dirname "$0")"
./dbcheck
STARTEOF
chmod +x "$BUILDDIR/start.sh"

# Create tar.gz
# 架构后缀取自构建容器的 uname -m：x86_64 保持既有产物名不变；
# aarch64（鲲鹏 / 飞腾 / 麒麟 arm64）输出独立包，避免两种架构互相覆盖。
TARGET_ARCH="$(uname -m)"
case "$TARGET_ARCH" in
    x86_64|amd64)  TARGET_ARCH="x86_64" ;;
    aarch64|arm64) TARGET_ARCH="aarch64" ;;
    *)
        echo "[ERROR] Unsupported build architecture: $TARGET_ARCH (expected x86_64 or aarch64)"
        exit 1
        ;;
esac
VERSION=$($PYTHON_CMD -c "import json; print(json.load(open('modules/config/version.json', encoding='utf-8-sig'))['version'])")
RELEASE_NAME="RaccoonX-Linux-$TARGET_ARCH-$VERSION"
cd dist
tar czf "$RELEASE_NAME.tar.gz" RaccoonX-Linux/
cd "$PROJECT_ROOT"

RELEASE_SIZE=$(du -sh "dist/$RELEASE_NAME.tar.gz" | cut -f1)
echo ""
echo "========================================"
echo "  Build complete!"
echo "  Release: dist/$RELEASE_NAME.tar.gz ($RELEASE_SIZE)"
echo "========================================"
echo ""
echo "Deploy ($TARGET_ARCH, glibc >= 2.28):"
echo "  ldd --version | head -1     # 确认 glibc 版本 >= 2.28"
echo "  tar xzvf $RELEASE_NAME.tar.gz"
echo "  cd RaccoonX-Linux && bash start.sh"
echo "Note: glibc 2.17 的旧系统请使用 Docker 多架构镜像；Oracle thick / GBase 原生驱动仅提供 x86_64 版本。"
