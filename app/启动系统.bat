@echo off
chcp 65001 >nul
title 业务高效运转系统
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
    echo    4. 打开解压后出现的【业务系统v12.2.2便携版】文件夹
    echo    5. 双击里面的「启动系统.bat」
    echo ============================================================
    echo.
    pause
    exit /b 1
)

cd /d "%CUR%app"

echo ============================================================
echo   业务高效运转系统
echo   正在启动，浏览器将自动打开 http://127.0.0.1:5000
echo   默认账号：admin / admin123
echo   关闭本窗口即停止系统
echo ============================================================
echo.

if not exist "%CUR%python\python.exe" (
    echo [错误] 未找到 python 运行环境，请确认是完整解压（含 python 文件夹）。
    pause
    exit /b 1
)

"%CUR%python\python.exe" app.py

echo.
echo 系统已停止运行。
pause
