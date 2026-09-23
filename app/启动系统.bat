@echo off
title Business Management System

echo ============================================================
echo   AI-Driven Business Operation System - Quick Start
echo ============================================================
echo.

cd /d "%~dp0"

echo [1/4] Checking Python environment...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python not found!
    echo Please install Python 3.8+ from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do echo       Python %%i OK

echo.
echo [2/4] Checking dependencies...
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo       Installing dependencies (first run, please wait)...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
    if errorlevel 1 (
        echo       Tsinghua mirror failed, trying official source...
        pip install -r requirements.txt >nul 2>&1
    )
    echo       Dependencies installed OK
) else (
    echo       Dependencies OK
)

echo.
echo [3/4] Checking database...
if not exist "business.db" (
    echo       First run, initializing database...
    python init_db.py >nul 2>&1
    echo       Database initialized (with sample data) OK
) else (
    echo       Database exists OK
)

echo.
echo [4/4] Starting system...
echo.
echo ============================================================
echo   System started successfully!
echo.
echo   Local:   http://127.0.0.1:5000
echo   Network: http://YOUR_IP:5000
echo.
echo   Browser will open automatically.
echo   Close this window to stop the system.
echo ============================================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5000"

python app.py --no-debug

echo.
echo System stopped.
pause
