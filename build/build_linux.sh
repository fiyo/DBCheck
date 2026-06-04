#!/bin/bash
# ============================================================
# DBCheck Linux Build Script
# Target: CentOS 7.9 / RHEL 7+
# Requires: Python >= 3.10, pip, gcc
# ============================================================
set -e

echo "========================================"
echo "  DBCheck Linux Build Script"
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
    echo "  Install Python 3.10+ on CentOS 7.9:"
    echo "    sudo yum install -y https://repo.ius.io/ius-release-el7.rpm"
    echo "    sudo yum install -y python310 python310-pip python310-devel"
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

# Check pyinstaller
if ! command -v pyinstaller &> /dev/null; then
    echo "  Installing pyinstaller..."
    pip install pyinstaller --quiet
fi

echo "[3/5] Installing project dependencies..."
pip install -r requirements.txt --quiet

echo "[4/5] Building executable..."
# Clean old build artifacts (NOT the build/ directory)
rm -rf dist __pycache__ build_pyinstaller_tmp
$PYTHON_CMD -m PyInstaller build/dbcheck_linux.spec --noconfirm

echo "[5/5] Packaging release..."

# Create start script
BUILDDIR="dist/DBCheck-Linux"
cat > "$BUILDDIR/start.sh" << 'STARTEOF'
#!/bin/bash
cd "$(dirname "$0")"
./dbcheck
STARTEOF
chmod +x "$BUILDDIR/start.sh"

# Create tar.gz
RELEASE_NAME="DBCheck-Linux-x86_64"
cd dist
tar czf "$RELEASE_NAME.tar.gz" DBCheck-Linux/
cd "$PROJECT_ROOT"

RELEASE_SIZE=$(du -sh "dist/$RELEASE_NAME.tar.gz" | cut -f1)
echo ""
echo "========================================"
echo "  Build complete!"
echo "  Release: dist/$RELEASE_NAME.tar.gz ($RELEASE_SIZE)"
echo "========================================"
echo ""
echo "Deploy to CentOS:"
echo "  tar xzvf $RELEASE_NAME.tar.gz"
echo "  cd DBCheck-Linux && bash start.sh"
