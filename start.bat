@echo off
REM ============================================
REM DBCheck 启动脚本 (含 RBAC 用户管理模块)
REM Windows 版本
REM ============================================

cd /d "%~dp0"

echo ==========================================
echo   DBCheck - 数据库健康巡检平台
echo   (RBAC 用户管理模块已集成)
echo ==========================================

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.9+
    pause
    exit /b 1
)

echo [OK] Python 环境已就绪

REM 安装依赖（如需要）
if not exist ".deps_installed" (
    echo.
    echo 安装项目依赖...
    pip install -r requirements.txt
    type nul > .deps_installed
    echo [OK] 依赖安装完成
)

REM 初始化 RBAC 种子数据
if not exist "pro_data\.rbac_seeded" (
    echo.
    echo 初始化 RBAC 用户管理系统...
    python -m user_management.seed
)

REM 启动应用
echo.
echo 启动 DBCheck Web 服务...
echo   访问地址: http://localhost:5003
echo   管理后台: http://localhost:5003/um/admin
echo   登录页面: http://localhost:5003/um/login
echo.
echo   默认管理员: admin / admin123
echo.

python web_ui.py
pause
