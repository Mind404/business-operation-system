@echo off
chcp 65001 >nul
title Business System
set "CUR=%~dp0"

set "INZIP=NO"
if not "%CUR:.zip\=%"=="%CUR%" set "INZIP=YES"
if "%INZIP%"=="YES" (
    cls
    echo ============================================================
    echo   [ERROR] Running directly from inside the zip archive!
    echo.
    echo   Please follow these steps:
    echo.
    echo   1. Close this window
    echo   2. Go back to your download folder
    echo   3. Right-click the zip file, select "Extract All"
    echo   4. Open the extracted folder
    echo   5. Double-click start.bat inside it
    echo ============================================================
    echo.
    pause
    exit /b 1
)

cd /d "%CUR%app"

echo ============================================================
echo   Business Operation System v13.0.0.1
echo   Starting... browser will open at http://127.0.0.1:5000
echo   Default account: admin / admin123
echo   Close this window to stop the system
echo ============================================================
echo.

if not exist "%CUR%python\python.exe" (
    echo [ERROR] python runtime not found. Please extract the full zip first.
    pause
    exit /b 1
)

"%CUR%python\python.exe" app.py

echo.
echo System stopped.
pause
