# DBCheck Release Script (simplified)
# Usage: .\release.ps1 -Version "2.5.6"
# GitHub Actions will handle Docker build/push and GitHub Release automatically.

param(
    [Parameter(Mandatory=$true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$VersionWithV = "v$Version"
$ProjectRoot = Split-Path $MyInvocation.MyCommand.Path

# Validate version format
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "ERROR: Version must be x.y.z (e.g. 2.5.6)" -ForegroundColor Red
    exit 1
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DBCheck Release" -ForegroundColor Cyan
Write-Host "  New Version: $VersionWithV" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Git status
Write-Host "[1/4] Checking Git status..." -ForegroundColor Yellow
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
Write-Host "[2/4] Pulling latest code..." -ForegroundColor Yellow
$stashed = $false
git diff --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Stashing uncommitted changes..." -ForegroundColor Yellow
    git stash --include-untracked 2>&1 | Out-Null
    $stashed = $true
}
git pull --rebase --quiet 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
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
Write-Host "[3/4] Updating version files..." -ForegroundColor Yellow

# Update version.py
$VersionPy = Join-Path $ProjectRoot "version.py"
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

# Update Dockerfile
$Dockerfile = Join-Path $ProjectRoot "Dockerfile"
if (Test-Path $Dockerfile) {
    $lines = Get-Content $Dockerfile -Encoding UTF8
    $newLines = @()
    foreach ($line in $lines) {
        if ($line -match 'RUN echo .* > /app/VERSION\.txt') {
            $newLines += "RUN echo $Version > /app/VERSION.txt"
        } else {
            $newLines += $line
        }
    }
    Set-Content $Dockerfile -Value $newLines -Encoding UTF8
    Write-Host "  OK: Dockerfile updated to $Version" -ForegroundColor Green
} else {
    Write-Host "  WARN: Dockerfile not found, skipped" -ForegroundColor Yellow
}

# Step 4: Commit, push, and create tag
Write-Host "[4/4] Committing, pushing, and creating tag..." -ForegroundColor Yellow

# Commit and push
git add -A
git diff --cached --quiet 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  WARN: Nothing to commit, skipping commit" -ForegroundColor Yellow
} else {
    git commit -m "Release $VersionWithV"
    git push origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: git push failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: Pushed to GitHub (main)" -ForegroundColor Green
}

# Delete tag if exists (for re-run, suppress error if not found)
try   { git tag -d $VersionWithV 2>&1 | Out-Null } catch {}
Write-Host "  (cleaned local tag if existed)" -ForegroundColor Gray
try   { git push origin :refs/tags/$VersionWithV 2>&1 | Out-Null } catch {}
Write-Host "  (cleaned remote tag if existed)" -ForegroundColor Gray

# Create and push tag (triggers GitHub Actions)
git tag $VersionWithV
git push origin $VersionWithV
if ($LASTEXITCODE -eq 0) {
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
