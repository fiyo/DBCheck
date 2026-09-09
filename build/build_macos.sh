#!/bin/bash
# ============================================================
# RaccoonX macOS Build Script
# Target: macOS 12+ (Monterey and later)
# Requires: Python >= 3.10, pip
# ============================================================
set -e

echo "========================================"
echo "  RaccoonX macOS Build Script"
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
    echo "  macOS: brew install python@3.10"
    exit 1
fi

PYTHON_VER=$($PYTHON_CMD -c "import sys; v=sys.version_info; print(str(v[0])+'.'+str(v[1]))")
echo "[1/5] Python version: $PYTHON_VER"

MAJOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.major)")
MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)")

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
    echo "[ERROR] Python >= 3.10 required (current: $PYTHON_VER)"
    echo "  Install Python 3.10+ on macOS:"
    echo "    brew install python@3.10"
    exit 1
fi

echo "[2/5] Checking dependencies..."

# Install system dependencies required by Python drivers
# (pyodbc needs unixodbc, which may not be present on fresh CI images)
if command -v brew &> /dev/null; then
    if [ -z "$(brew list unixodbc 2>/dev/null)" ]; then
        echo "  Installing unixodbc (required by pyodbc)..."
        brew install unixodbc
    fi
fi

# Create virtual environment
VENV_DIR="$PROJECT_ROOT/.venv_build"
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    $PYTHON_CMD -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Check pyinstaller
if ! command -v pyinstaller &> /dev/null; then
    echo "  Installing pyinstaller..."
    pip install pyinstaller --quiet
fi

echo "[3/5] Installing project dependencies..."
# CI 宽松模式（DBCHECK_CI_LENIENT=1）：逐个依赖安装，装不上的可选驱动跳过
# （macOS 上 dmpython 无 wheel、gbase8sdb 只有 sdist 且需厂商 SDK），
# 最后统一 import 校验构建期必需模块。未设该变量时行为与原先完全一致。
if [ "${DBCHECK_CI_LENIENT:-0}" = "1" ]; then
    "$PYTHON_CMD" build/ci_install_deps.py
else
    pip install -r deploy/requirements.txt --quiet
fi

echo "[4/5] Building executable..."
# Clean old build artifacts (NOT the build/ directory)
rm -rf dist __pycache__ build_pyinstaller_tmp
$PYTHON_CMD -m PyInstaller build/dbcheck_macos.spec --noconfirm

echo "[5/5] Packaging release..."

# Determine architecture (normalize aarch64/arm64 -> arm64, x86_64 stays)
ARCH=$(uname -m)
case "$ARCH" in
    aarch64|arm64) ARCH="arm64" ;;
    x86_64) ARCH="x86_64" ;;
esac
VERSION=$($PYTHON_CMD -c "import json; print(json.load(open('modules/config/version.json', encoding='utf-8-sig'))['version'])")
RELEASE_NAME="RaccoonX-macOS-$ARCH-$VERSION"

# Create start script
BUILDDIR="dist/RaccoonX-macOS"
cat > "$BUILDDIR/start.sh" << 'STARTEOF'
#!/bin/bash
cd "$(dirname "$0")"
./dbcheck
STARTEOF
chmod +x "$BUILDDIR/start.sh"

# Create tar.gz
cd dist
tar czf "$RELEASE_NAME.tar.gz" RaccoonX-macOS/
cd "$PROJECT_ROOT"

RELEASE_SIZE=$(du -sh "dist/$RELEASE_NAME.tar.gz" | cut -f1)
echo ""
echo "========================================"
echo "  Build complete!"
echo "  Release: dist/$RELEASE_NAME.tar.gz ($RELEASE_SIZE)"
echo "========================================"
echo ""
echo "Deploy to macOS:"
echo "  tar xzvf $RELEASE_NAME.tar.gz"
echo "  cd RaccoonX-macOS && bash start.sh"
