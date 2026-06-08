<# ============================================================
  DBCheck 版本发布脚本 (PowerShell)
  Usage: .\release.ps1 -Version "2.5.4"
  ============================================================ #>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true, HelpMessage="New version number, e.g. 2.5.4")]
    [string]$Version
)

$ErrorActionPreference = "Stop"

# ── 版本号格式验证 ──────────────────────────────────────────
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "❌ 版本号格式错误！正确格式：X.Y.Z（如 2.5.4）" -ForegroundColor Red
    exit 1
}

$VersionWithV = "v$Version"   # v2.5.4
$ProjectRoot = $PSScriptRoot

Write-Host "══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  DBCheck 版本发布" -ForegroundColor Cyan
Write-Host "  新版本: $VersionWithV" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── 1. 检查 Git 状态 ────────────────────────────────────────
Write-Host "[1/7] 检查 Git 状态..." -ForegroundColor Yellow
Set-Location $ProjectRoot
git update-index --refresh 2>$null
$Status = git status --porcelain
if ($Status) {
    Write-Host "⚠️  有未提交的更改，请先提交或暂存：" -ForegroundColor Yellow
    $Status | ForEach-Object { Write-Host "  $_" }
    $Confirm = Read-Host "是否继续？(y/N)"
    if ($Confirm -ne "y" -and $Confirm -ne "Y") {
        Write-Host "❌ 已取消" -ForegroundColor Red
        exit 1
    }
}

# ── 2. 拉取最新代码 ──────────────────────────────────────────
Write-Host "[2/7] 拉取最新代码..." -ForegroundColor Yellow
git pull --rebase 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ git pull 失败，请手动解决冲突" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ 已拉取最新代码" -ForegroundColor Green

# ── 3. 更新 version.py ───────────────────────────────────────
Write-Host "[3/7] 更新 version.py (__version__ = '$VersionWithV')..." -ForegroundColor Yellow
$VersionPy = Join-Path $ProjectRoot "version.py"
if (Test-Path $VersionPy) {
    (Get-Content $VersionPy) -replace "^__version__\s*=\s*"".*"""", "__version__ = ""$VersionWithV""" | Set-Content $VersionPy -Encoding UTF8
    Write-Host "  ✓ version.py 已更新" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  version.py 不存在，跳过" -ForegroundColor Yellow
}

# ── 4. 更新 Dockerfile VERSION.txt ───────────────────────────
Write-Host "[4/7] 更新 Dockerfile (VERSION.txt = '$Version')..." -ForegroundColor Yellow
$Dockerfile = Join-Path $ProjectRoot "Dockerfile"
if (Test-Path $Dockerfile) {
    (Get-Content $Dockerfile) -replace 'RUN echo "[\d\.]+" > /app/VERSION\.txt', "RUN echo ""$Version"" > /app/VERSION.txt" | Set-Content $Dockerfile -Encoding UTF8
    Write-Host "  ✓ Dockerfile 已更新" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  Dockerfile 不存在，跳过" -ForegroundColor Yellow
}

# ── 5. 提交并推送代码 ────────────────────────────────────────
Write-Host "[5/7] 提交并推送代码..." -ForegroundColor Yellow
git add version.py Dockerfile release.ps1 2>$null
git add -A 2>$null
$CommitMsg = "Release v$Version"
git commit -m $CommitMsg 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ⚠️  没有需要提交的更改" -ForegroundColor Yellow
} else {
    git push origin main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ git push 失败" -ForegroundColor Red
        exit 1
    }
    Write-Host "  ✓ 代码已推送" -ForegroundColor Green
}

# ── 6. 打 Tag 并推送 ────────────────────────────────────────
Write-Host "[6/7] 打 Tag '$VersionWithV' 并推送..." -ForegroundColor Yellow
# 删除本地旧 tag（如果存在）
git tag -d $VersionWithV 2>$null
# 删除远程旧 tag（如果存在）
git push origin ":refs/tags/$VersionWithV" 2>$null
# 打新 tag
git tag $VersionWithV
git push origin $VersionWithV 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ git push tag 失败" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ Tag '$VersionWithV' 已推送" -ForegroundColor Green

# ── 7. 输出后续操作说明 ────────────────────────────────────
Write-Host ""
Write-Host "══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✅ 版本 $VersionWithV 发布完成！" -ForegroundColor Green
Write-Host "══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "📦 GitHub Actions 正在构建 Docker 镜像..." -ForegroundColor Cyan
Write-Host "   查看进度: https://github.com/fiyo/DBCheck/actions" -ForegroundColor Cyan
Write-Host ""
Write-Host "🐳 构建完成后拉取镜像：" -ForegroundColor Cyan
Write-Host "   docker pull jackge12345/dbcheck:$Version" -ForegroundColor White
Write-Host "   docker pull ghcr.io/fiyo/dbcheck:$Version" -ForegroundColor White
Write-Host ""
Write-Host "📝 创建 GitHub Release（可选）：" -ForegroundColor Cyan
Write-Host "   https://github.com/fiyo/DBCheck/releases/new?tag=$VersionWithV" -ForegroundColor White
Write-Host ""
