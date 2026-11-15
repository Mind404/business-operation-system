@echo off
chcp 65001 >nul
title v13.0.0.1 - Diagnose
cd /d "%~dp0"

if not exist "%~dp0python\python.exe" (
    echo [ERROR] python runtime not found. Please extract the full zip first.
    pause
    exit /b 1
)

echo Running self-check, please wait...
echo.
"%~dp0python\python.exe" "%~dp0app\app.py" --selfcheck
echo.
echo ------------------------------------------------------------
echo  If "table check warnings = 0", the platform is healthy.
echo  If a plugin shows enabled = NO, open Plugin Manager and enable it.
echo ------------------------------------------------------------
pause
