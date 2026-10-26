@echo off
chcp 65001 >nul
title Install as Windows Service (nssm)

echo ============================================================
echo   Install as Windows Service (nssm required)
echo ============================================================
echo.
echo This method supports:
echo   - Auto start on Windows boot
echo   - Auto restart on crash
echo   - Run in background (no window)
echo.
echo REQUIREMENT: nssm.exe must be in this folder or in PATH.
echo Download: https://nssm.cc/download
echo.

set "APP_DIR=%~dp0"
set "NSSM=%APP_DIR%nssm.exe"
set "PYTHON=python"
set "APP_SCRIPT=%APP_DIR%app.py"
set "SERVICE_NAME=BusinessSystem"

where nssm >nul 2>&1
if %errorlevel% neq 0 (
    if not exist "%NSSM%" (
        echo [ERROR] nssm.exe not found!
        echo.
        echo Please download nssm from https://nssm.cc/download
        echo and extract nssm.exe to this folder:
        echo %APP_DIR%
        echo.
        echo Alternatively, use "设置开机自启.bat" (no nssm needed).
        pause
        exit /b 1
    )
) else (
    set "NSSM=nssm"
)

echo Installing service: %SERVICE_NAME%
echo.

"%NSSM%" install %SERVICE_NAME% "%PYTHON%" "%APP_SCRIPT% --no-debug"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install service.
    echo Please run this script as Administrator.
    pause
    exit /b 1
)

"%NSSM%" set %SERVICE_NAME% AppDirectory "%APP_DIR%"
"%NSSM%" set %SERVICE_NAME% AppStdout "%APP_DIR%logs\service_out.log"
"%NSSM%" set %SERVICE_NAME% AppStderr "%APP_DIR%logs\service_err.log"
"%NSSM%" set %SERVICE_NAME% AppRotateFiles 1
"%NSSM%" set %SERVICE_NAME% AppRotateBytes 10485760
"%NSSM%" set %SERVICE_NAME% Start SERVICE_AUTO_START
"%NSSM%" set %SERVICE_NAME% AppRestartDelay 5000
"%NSSM%" set %SERVICE_NAME% AppThrottle 5000

if not exist "%APP_DIR%logs" mkdir "%APP_DIR%logs"

echo.
echo Starting service...
net start %SERVICE_NAME%

echo.
echo ============================================================
echo   Service installed and started successfully!
echo ============================================================
echo.
echo Service name: %SERVICE_NAME%
echo Status: Running
echo Access: http://127.0.0.1:5000
echo.
echo Management commands (run as Administrator):
echo   Start:  net start %SERVICE_NAME%
echo   Stop:   net stop %SERVICE_NAME%
echo   Remove: nssm remove %SERVICE_NAME% confirm
echo.
echo Logs: %APP_DIR%logs\
echo.
pause
