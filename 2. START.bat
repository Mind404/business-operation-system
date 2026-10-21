@echo off
chcp 65001 >nul
title Business Efficient Operation System
echo ============================================================
echo   Business Efficient Operation System
echo ============================================================
echo.
cd /d "%~dp0app"
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found
    echo Please run "1. SETUP.bat" first
    pause
    exit /b 1
)
echo Starting system...
echo.
echo Local URL:   http://127.0.0.1:5000
echo Network URL: http://[your-IP]:5000
echo.
echo Do NOT close this window while using the system.
echo.
python app.py --port 5000
echo.
echo System stopped.
pause
