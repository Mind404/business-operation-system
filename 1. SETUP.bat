@echo off
chcp 65001 >nul
title Business System - Setup
echo ============================================================
echo   Business System - Environment Setup
echo ============================================================
echo.
where python >nul 2>nul
if %errorlevel%==0 (
    echo [OK] Python detected
    python --version
) else (
    echo [ERROR] Python not found
    echo Please install Python 3.8+ first
    pause
    exit /b 1
)
echo.
echo Installing dependencies...
pip install flask openpyxl -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo ============================================================
echo   Setup complete!
echo   Please double-click "2. START.bat" to start
echo ============================================================
pause
