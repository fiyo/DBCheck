# DBCheck Release Script (simplified)
# Usage: .\release.ps1 -Version "26.9.9"  (or date-based "26.7.8.1")
# GitHub Actions will handle Docker build/push and GitHub Release automatically.

param(
    [Parameter(Mandatory=$true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$VersionWithV = "v$Version"
$ProjectRoot = Split-Path $MyInvocation.MyCommand.Path

# 健壮的 git 网络封装：先走本地代理（127.0.0.1:9999），失败后自动回退直连 SSH。
# 同时规避 $ErrorActionPreference='Stop' 下原生 git 写 stderr 抛 NativeCommandError 导致脚本中断。
function Invoke-GitNet {
    param([Parameter(Mandatory=$true)][string[]]$ArgumentList)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & git @ArgumentList 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { return 0 }
        Write-Host "  WARN: git $($ArgumentList -join ' ') 经代理失败 (exit $LASTEXITCODE)，回退直连 SSH 重试..." -ForegroundColor Yellow
        $oldSsh = $env:GIT_SSH_COMMAND
        $env:GIT_SSH_COMMAND = "ssh -o ProxyCommand=none -o ConnectTimeout=15"
        try {
            & git @ArgumentList 2>&1 | Out-Null
        } finally {
            if ($null -eq $oldSsh) { Remove-Item Env:GIT_SSH_COMMAND -ErrorAction SilentlyContinue } else { $env:GIT_SSH_COMMAND = $oldSsh }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

# Validate version format (supports x.y.z and x.y.z.n, e.g. 2.5.6 or 26.7.8.1)
if ($Version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') {
    Write-Host "ERROR: Version must be x.y.z or x.y.z.n (e.g. 2.5.6 or 26.7.8.1)" -ForegroundColor Red
    exit 1
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DBCheck Release" -ForegroundColor Cyan
Write-Host "  New Version: $VersionWithV" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Git status
Write-Host "[1/5] Checking Git status..." -ForegroundColor Yellow
$gitStatus = git status --porcelain
if ($gitStatus) {
    Write-Host "WARNING: Uncommitted changes found:" -ForegroundColor Yellow
    $gitStatus -split "`n" | ForEach-Object { Write-Host "   $_" -ForegroundColor Gray }
    $Confirm = Read-Host "Continue? (y/N)"
    if ($Confirm -ne "y" -and $Confirm -ne "Y") {
        Write-Host "Aborted." -ForegroundColor Yellow
        exit 0
    }
}

# Step 2: Pull latest code (stash if needed)
Write-Host "[2/5] Pulling latest code..." -ForegroundColor Yellow
$stashed = $false
git diff --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Stashing uncommitted changes..." -ForegroundColor Yellow
    git stash --include-untracked 2>&1 | Out-Null
    $stashed = $true
}
$pullCode = Invoke-GitNet -ArgumentList @('pull','--rebase','--quiet')
if ($pullCode -ne 0) {
    Write-Host "ERROR: git pull failed" -ForegroundColor Red
    if ($stashed) { git stash pop 2>&1 | Out-Null }
    exit 1
}
if ($stashed) {
    Write-Host "  Restoring stashed changes..." -ForegroundColor Yellow
    git stash pop 2>&1 | Out-Null
}
Write-Host "  OK: Pulled latest code" -ForegroundColor Green

# Step 3: Update version.py and Dockerfile
Write-Host "[3/5] Updating version files..." -ForegroundColor Yellow

# Update version.py
$VersionPy = Join-Path $ProjectRoot "..\modules\config\version.py"
if (Test-Path $VersionPy) {
    $lines = Get-Content $VersionPy -Encoding UTF8
    $newLines = @()
    foreach ($line in $lines) {
        if ($line -match '__version__\s*=') {
            $newLines += "__version__ = '$VersionWithV'"
        } else {
            $newLines += $line
        }
    }
    Set-Content $VersionPy -Value $newLines -Encoding UTF8
    Write-Host "  OK: version.py updated to $VersionWithV" -ForegroundColor Green
} else {
    Write-Host "  WARN: version.py not found, skipped" -ForegroundColor Yellow
}

# NOTE: Dockerfile 内 VERSION.txt 由构建参数 DBCHECK_VERSION 驱动
# （RUN echo "${DBCHECK_VERSION#v}"）。CI 与 scripts/build-multiarch.sh 会从
# modules/config/version.py 解析版本并以 --build-arg 传入，此处无需改写 Dockerfile。

# Step 4: Generate release notes append (consumed by build-release.yml)
Write-Host "[4/5] Generating release notes append..." -ForegroundColor Yellow

$ReleaseAppendDir = Join-Path $ProjectRoot "..\.github"
$ReleaseAppendPath = Join-Path $ReleaseAppendDir "release_append.md"
if (-not (Test-Path $ReleaseAppendDir)) {
    New-Item -ItemType Directory -Path $ReleaseAppendDir -Force | Out-Null
}

$ReleaseAppend = @"
---

## ✨ 主要更新
- 接入 BIC-QA 云端知识库（官方 `/open-api/v1`，SSE 流式返回），作为本地 12 专家的能力补充。
- 智能诊断中心 BIC-QA 页 Tab 重排、国际化、Markdown 表格渲染、诊断发现导入等 UI/UX 修复。

## 🐳 Docker 镜像

推荐使用 Docker Hub（国内可用）：

```powershell
docker pull jackge12345/dbcheck:$VersionWithV
docker pull jackge12345/dbcheck:latest
```

或 GitHub Container Registry：

```powershell
docker pull ghcr.io/fiyo/dbcheck:$VersionWithV
docker pull ghcr.io/fiyo/dbcheck:latest
```

运行示例：

```powershell
docker run -d -p 5003:5003 --name raccoonx jackge12345/dbcheck:$VersionWithV
```

> 镜像同时支持 `linux/amd64` 与 `linux/arm64`（ARM64 信创主机）。

## ⚠️ 安装注意事项
- **macOS**：当前分发包**未做 Apple 公证（二进制未签名）**。首次打开若被 Gatekeeper 拦截，请右键点击 App / 可执行文件 →「打开」并在弹窗中确认即可运行；如需彻底消除提示，后续可接入 Apple 开发者证书做正式公证。
- **Windows**：解压后双击 `start.bat` 启动，浏览器访问 http://localhost:5003 。
- **Linux**：客户端安装包暂未发布，当前仅提供 Windows / macOS 安装包；Linux 用户请直接使用上方 Docker 镜像。

## 📦 二进制包
下方 Assets 提供 Windows / macOS 客户端压缩包；需要源码编译的请下载 `Source code`。
"@

Set-Content -Path $ReleaseAppendPath -Value $ReleaseAppend -Encoding UTF8
Write-Host "  OK: release_append.md generated at .github/release_append.md" -ForegroundColor Green

# Step 5: Commit, push, and create tag
Write-Host "[5/5] Committing, pushing, and creating tag..." -ForegroundColor Yellow

# Commit and push version file + release notes append (avoid staging runtime data/ or untracked files)
git add modules/config/version.py .github/release_append.md
git diff --cached --quiet 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  WARN: Nothing to commit, skipping commit" -ForegroundColor Yellow
} else {
    git commit -m "Release $VersionWithV"
    $pushCode = Invoke-GitNet -ArgumentList @('push','origin','main')
    if ($pushCode -ne 0) {
        Write-Host "ERROR: git push failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: Pushed to GitHub (main)" -ForegroundColor Green
}

# Delete tag if exists (for re-run, suppress error if not found)
try   { git tag -d $VersionWithV 2>&1 | Out-Null } catch {}
Write-Host "  (cleaned local tag if existed)" -ForegroundColor Gray
# 删除远程旧 tag：必须走 Invoke-GitNet（代理失败自动回退直连 SSH）。
# 此前这里用裸 git push 经挂掉的代理失败、且被 try/catch 静默吞掉，
# 导致远程旧 tag 未删除 → 后续 tag 推送非快进被拒（exit 128）。
$delTagCode = Invoke-GitNet -ArgumentList @('push','origin',":refs/tags/$VersionWithV")
if ($delTagCode -ne 0) {
    Write-Host "  WARN: 删除远程旧 tag 失败 (exit $delTagCode)，将尝试强制推送 tag" -ForegroundColor Yellow
}
Write-Host "  (cleaned remote tag if existed)" -ForegroundColor Gray

# Create and push tag (triggers GitHub Actions)
git tag $VersionWithV
$tagPushCode = Invoke-GitNet -ArgumentList @('push','origin',$VersionWithV)
if ($tagPushCode -ne 0) {
    # 兜底：远程可能仍存在同名旧 tag（删除未生效等），强制推送覆盖。
    Write-Host "  WARN: 普通 tag 推送失败 (exit $tagPushCode)，回退强制推送..." -ForegroundColor Yellow
    $tagPushCode = Invoke-GitNet -ArgumentList @('push','--force','origin',$VersionWithV)
}
if ($tagPushCode -eq 0) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  Release $VersionWithV tagged successfully!" -ForegroundColor Green
    Write-Host "  GitHub Actions is building and releasing..." -ForegroundColor Green
    Write-Host "  Watch progress: https://github.com/fiyo/DBCheck/actions" -ForegroundColor White
    Write-Host "  Release will be at: https://github.com/fiyo/DBCheck/releases/tag/$VersionWithV" -ForegroundColor White
    Write-Host "  Docker Hub: https://hub.docker.com/r/jackge12345/dbcheck/tags" -ForegroundColor White
    Write-Host "============================================" -ForegroundColor Green
} else {
    Write-Host "ERROR: Failed to push tag" -ForegroundColor Red
    exit 1
}

