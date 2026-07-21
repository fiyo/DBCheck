@echo off
REM ============================================================================
REM MongoDB 测试实例启动脚本（Windows / cmd）
REM 用于端到端验证 DBCheck MongoDB 巡检
REM
REM 用法（在 cmd 中）：
REM   set PASSWORD=YourStr0ngPwd && scripts\mongo-test-standalone.bat
REM   或直接双击/运行 scripts\mongo-test-standalone.bat   （使用默认密码）
REM
REM 密码设置：通过 MONGO_INITDB_ROOT_USERNAME / MONGO_INITDB_ROOT_PASSWORD
REM           两个环境变量在 admin 库创建一个 root 账号（这就是密码怎么设）
REM ============================================================================

set CONTAINER=mongo-test
set IMAGE=mongo:7
set PORT=27017
set USER=admin
if not defined PASSWORD set PASSWORD=DBCheck@2026

echo [INFO] 启动 MongoDB 测试实例 (%IMAGE%)，容器名 = %CONTAINER%
docker rm -f %CONTAINER% >nul 2>&1

docker run -d --name %CONTAINER% -p %PORT%:27017 -e MONGO_INITDB_ROOT_USERNAME=%USER% -e MONGO_INITDB_ROOT_PASSWORD=%PASSWORD% %IMAGE%

echo [INFO] 等待实例就绪 (Waiting for connections) ...
for /L %%i in (1,1,30) do (
  docker logs %CONTAINER% 2>&1 | findstr /C:"Waiting for connections" >nul && (
    echo [INFO] MongoDB 已就绪
    goto :ready
  )
  timeout /t 1 >nul
)
:ready

echo.
echo ==================================================================
echo  MongoDB 测试实例已启动
echo    镜像      : %IMAGE%
echo    地址      : localhost:%PORT%
echo    用户名    : %USER%
echo    密码      : %PASSWORD%
echo    认证库    : admin
echo    认证机制  : SCRAM-SHA-256 (Mongo 7 默认)
echo  ----------------------------------------------------------------
echo  在 DBCheck Web UI 添加 MongoDB 数据源时填写：
echo    host           = localhost
echo    port           = %PORT%
echo    user           = %USER%
echo    password       = %PASSWORD%
echo    auth_source    = admin
echo    auth_mechanism = SCRAM-SHA-256
echo  ==================================================================
echo.
echo  查看日志 : docker logs -f %CONTAINER%
echo  停止实例 : docker rm -f %CONTAINER%
