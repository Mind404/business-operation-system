@echo off
chcp 65001 >nul
title 业务高效运转系统(HTTPS模式)
set "CUR=%~dp0"

:: 检测是否在压缩包内直接运行（路径含 .zip\）
set "INZIP=NO"
if not "%CUR:.zip\=%"=="%CUR%" set "INZIP=YES"
if "%INZIP%"=="YES" (
    cls
    echo ============================================================
    echo    [重要] 你正在压缩包里面直接运行！
    echo.
    echo    这样会导致程序运行不稳定。请按以下步骤操作：
    echo.
    echo    1. 关闭本窗口
    echo    2. 回到下载文件夹，找到这个 zip 压缩包
    echo    3. 右键点击它，选择「全部解压」
    echo    4. 打开解压后出现的【业务系统v12.4.2便携版】文件夹
    echo    5. 双击里面的「启动系统.bat」
    echo ============================================================
    echo.
    pause
    exit /b 1
)

cd /d "%CUR%app"

echo ============================================================
echo   业务高效运转系统
echo   正在启动 HTTPS 模式，浏览器将自动打开 https://127.0.0.1:5000
echo   默认账号：admin / admin123
echo   关闭本窗口即停止系统
echo ============================================================
echo.

:: 检测 HTTPS 证书是否已装入本机受信任根（仅提示，不阻塞启动）
certutil -user -store Root business.local >nul 2>&1
if %errorlevel%==0 (
    echo   [证书] 已装入受信任根，浏览器可直接访问 https://127.0.0.1:5000
) else (
    echo   [重要] 本机尚未安装本系统 HTTPS 证书！
    echo          请先双击「安装HTTPS证书.bat」完成安装，
    echo          否则浏览器访问 https://127.0.0.1:5000 会超时打不开。
    echo          装完证书后，重新双击本 bat 启动即可。
)
echo.

if not exist "%CUR%python\python.exe" (
    echo [错误] 未找到 python 运行环境，请确认是完整解压（含 python 文件夹）。
    pause
    exit /b 1
)

echo.
echo   （启动日志同时写入 app\https_启动日志.txt，若闪退请把该文件发开发人员）
"%CUR%python\python.exe" app.py --https > "%CUR%app\https_启动日志.txt" 2>&1

echo.
echo 系统已停止运行。
pause
