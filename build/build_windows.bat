@echo off
chcp 437 >nul
title RaccoonX Windows Build
cd /d "%~dp0.."

:: Use temp directory for PyInstaller intermediate files
:: (avoid conflict with our build/ script directory)
set WORKPATH=%TEMP%\dbcheck_build_%RANDOM%
set DISTPATH=dist

echo ==========================================
echo   RaccoonX Windows Build Script
echo   Target: Windows x64
echo ==========================================
echo.

:: --- Check Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+.
    if not defined GITHUB_ACTIONS pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python -c "import sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor))"') do set PYVER=%%v
echo [1/5] Python version: %PYVER%

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python >= 3.10 required. Current: %PYVER%
    if not defined GITHUB_ACTIONS pause
    exit /b 1
)

:: --- Read version ---
for /f "tokens=*" %%v in ('python -c "import json,sys; d=json.load(open('modules/config/version.json','r',encoding='utf-8-sig')); print(d['version'])"') do set VERSION=%%v
if not defined VERSION set VERSION=v26.9.11.0
echo [    ] RaccoonX version: %VERSION%
echo.

:: --- Install dependencies ---
echo [2/5] Installing dependencies (this may take a few minutes)...
echo [    ] You will see pip progress output below.
echo.
:: CI 宽松模式：逐个依赖安装，装不上的可选驱动（依赖厂商 SDK 的厂商驱动等）跳过，
:: 最后统一 import 校验构建期必需模块。未设该变量时行为与原先完全一致。
if defined DBCHECK_CI_LENIENT (
    echo [    ] CI lenient mode: installing dependencies one-by-one.
    python build\ci_install_deps.py
) else (
    pip install -r deploy/requirements.txt
)
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    if not defined GITHUB_ACTIONS pause
    exit /b 1
)
echo.

echo [    ] Installing PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    if not defined GITHUB_ACTIONS pause
    exit /b 1
)
echo.

:: --- Clean previous builds ---
echo [3/5] Cleaning previous build artifacts...

:: Only clean dist/ and __pycache__ (NOT build/ which holds our scripts)
if exist "dist\" (
    rmdir /s /q "dist" 2>nul
)
for /d /r %%d in (__pycache__) do (
    if exist "%%d\" rmdir /s /q "%%d" 2>nul
)
echo [OK] Clean complete.
echo.

:: --- Build ---
echo [4/5] Building executable...
echo [    ] This takes 2-5 minutes. Please wait...
echo [    ] Intermediate files: %WORKPATH%
echo.

pyinstaller --clean --workpath "%WORKPATH%" --distpath "%DISTPATH%" build\dbcheck_windows.spec

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed!
    echo [    ] Common fix: close any running RaccoonX instances, then retry.
    if not defined GITHUB_ACTIONS pause
    exit /b 1
)

:: Clean up intermediate files
if exist "%WORKPATH%" (
    rmdir /s /q "%WORKPATH%" 2>nul
)

:: Check output
if not exist "%DISTPATH%\RaccoonX-Windows" (
    echo [ERROR] Build output not found at %DISTPATH%\RaccoonX-Windows
    if not defined GITHUB_ACTIONS pause
    exit /b 1
)
echo.

:: --- Package ---
echo [5/5] Packaging distribution...
python build\package_windows.py "%DISTPATH%" "%VERSION%"
if errorlevel 1 (
    echo [ERROR] Packaging failed.
    if not defined GITHUB_ACTIONS pause
    exit /b 1
)

echo.
echo ==========================================
echo   BUILD SUCCESS!
echo ==========================================
echo.
if not defined GITHUB_ACTIONS pause
