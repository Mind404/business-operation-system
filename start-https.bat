@echo off
chcp 65001 >nul
title Business System (HTTPS)
set "CUR=%~dp0"

set "INZIP=NO"
if not "%CUR:.zip\=%"=="%CUR%" set "INZIP=YES"
if "%INZIP%"=="YES" (
    cls
    echo [ERROR] Running from inside zip. Please extract first.
    pause
    exit /b 1
)

cd /d "%CUR%app"

echo ============================================================
echo   Business Operation System v12.7.1 (HTTPS mode)
echo   Starting at https://127.0.0.1:5443
echo   Default account: admin / admin123
echo ============================================================
echo.

if not exist "%CUR%python\python.exe" (
    echo [ERROR] python runtime not found.
    pause
    exit /b 1
)

"%CUR%python\python.exe" app.py --https

echo.
echo System stopped.
pause
