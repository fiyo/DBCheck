#!/usr/bin/env bash
# ============================================================
#  DBCheck 版本发布脚本 (Bash)
#  Usage: bash release.sh 2.5.4
# ============================================================

set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "❌ 用法: $0 <版本号>"
    echo "   示例: $0 2.5.4"
    exit 1
fi

# 版本号格式验证
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "❌ 版本号格式错误！正确格式：X.Y.Z（如 2.5.4）"
    exit 1
fi

VERSION_WITH_V="v$VERSION"
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "════════════════════════════════════════════════"
echo "  DBCheck 版本发布"
echo "  新版本: $VERSION_WITH_V"
echo "════════════════════════════════════════════════"
echo ""

# ── 1. 检查 Git 状态 ──────────────────────────────────
echo "[1/7] 检查 Git 状态..."
cd "$PROJECT_ROOT"
git update-index --refresh 2>/dev/null
if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
    echo "⚠️  有未提交的更改，请先提交或暂存："
    git status --short
    read -p "是否继续？(y/N) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "❌ 已取消"
        exit 1
    fi
fi

# ── 2. 拉取最新代码 ──────────────────────────────────
echo "[2/7] 拉取最新代码..."
if ! git pull --rebase 2>&1; then
    echo "❌ git pull 失败，请手动解决冲突"
    exit 1
fi
echo "  ✓ 已拉取最新代码"

# ── 3. 更新 version.py ─────────────────────────────────
echo "[3/7] 更新 version.py (__version__ = '$VERSION_WITH_V')..."
VERSION_PY="$PROJECT_ROOT/version.py"
if [[ -f "$VERSION_PY" ]]; then
    sed -i 's/^__version__\s*=.*/__version__ = "'"$VERSION_WITH_V"'"/' "$VERSION_PY"
    echo "  ✓ version.py 已更新"
else
    echo "  ⚠️  version.py 不存在，跳过"
fi

# ── 4. 更新 Dockerfile VERSION.txt ───────────────────
echo "[4/7] 更新 Dockerfile (VERSION.txt = '$VERSION')..."
DOCKERFILE="$PROJECT_ROOT/Dockerfile"
if [[ -f "$DOCKERFILE" ]]; then
    sed -i 's|RUN echo "[0-9.]*" > /app/VERSION\.txt|RUN echo "'"$VERSION"'" > /app/VERSION.txt|' "$DOCKERFILE"
    echo "  ✓ Dockerfile 已更新"
else
    echo "  ⚠️  Dockerfile 不存在，跳过"
fi

# ── 5. 提交并推送代码 ────────────────────────────────
echo "[5/7] 提交并推送代码..."
git add version.py Dockerfile release.sh 2>/dev/null
git add -A 2>/dev/null
COMMIT_MSG="Release $VERSION_WITH_V"
if git diff --cached --quiet 2>/dev/null; then
    echo "  ⚠️  没有需要提交的更改"
else
    git commit -m "$COMMIT_MSG"
    if ! git push origin main 2>&1; then
        echo "❌ git push 失败"
        exit 1
    fi
    echo "  ✓ 代码已推送"
fi

# ── 6. 打 Tag 并推送 ────────────────────────────────
echo "[6/7] 打 Tag '$VERSION_WITH_V' 并推送..."
# 删除本地旧 tag（如果存在）
git tag -d "$VERSION_WITH_V" 2>/dev/null || true
# 删除远程旧 tag（如果存在）
git push origin ":refs/tags/$VERSION_WITH_V" 2>/dev/null || true
# 打新 tag
git tag "$VERSION_WITH_V"
if ! git push origin "$VERSION_WITH_V" 2>&1; then
    echo "❌ git push tag 失败"
    exit 1
fi
echo "  ✓ Tag '$VERSION_WITH_V' 已推送"

# ── 7. 输出后续操作说明 ──────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo "  ✅ 版本 $VERSION_WITH_V 发布完成！"
echo "════════════════════════════════════════════════"
echo ""
echo "📦 GitHub Actions 正在构建 Docker 镜像..."
echo "   查看进度: https://github.com/fiyo/DBCheck/actions"
echo ""
echo "🐳 构建完成后拉取镜像："
echo "   docker pull jackge12345/dbcheck:$VERSION"
echo "   docker pull ghcr.io/fiyo/dbcheck:$VERSION"
echo ""
echo "📝 创建 GitHub Release（可选）："
echo "   https://github.com/fiyo/DBCheck/releases/new?tag=$VERSION_WITH_V"
echo ""
