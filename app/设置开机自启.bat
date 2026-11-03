@echo off
chcp 65001 >nul
title Setup Auto Start on Boot

echo ============================================================
echo   Setup Auto Start (Windows Task Scheduler)
echo ============================================================
echo.
echo This will start the system automatically when user logs in.
echo For crash auto-restart, please use nssm service method.
echo.

set "APP_DIR=%~dp0"
set "START_SCRIPT=%APP_DIR%后台启动(无窗口).vbs"

if not exist "%START_SCRIPT%" (
    echo [ERROR] Start script not found: %START_SCRIPT%
    pause
    exit /b 1
)

echo Creating scheduled task: Auto start on user logon
echo.

schtasks /create /tn "BusinessSystem_AutoStart" /tr "wscript.exe \"%START_SCRIPT%\"" /sc onlogon /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] Auto-start task created successfully!
    echo Task name: BusinessSystem_AutoStart
    echo Trigger: On user logon
    echo Start script: %START_SCRIPT%
    echo.
    echo The system will start automatically next time you log in.
    echo To start now, double-click: 启动系统.bat
    echo To view task: schtasks /query /tn BusinessSystem_AutoStart
    echo To delete task: schtasks /delete /tn BusinessSystem_AutoStart /f
) else (
    echo.
    echo [ERROR] Failed to create scheduled task.
    echo Please run this script as Administrator.
)

echo.
pause
