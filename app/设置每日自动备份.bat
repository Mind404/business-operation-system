@echo off
chcp 65001 >nul
title Setup Auto Backup

echo ============================================================
echo   Setup Daily Auto Backup
echo ============================================================
echo.

set "APP_DIR=%~dp0"
set "BACKUP_SCRIPT=%APP_DIR%备份数据库.bat"

if not exist "%BACKUP_SCRIPT%" (
    echo [ERROR] Backup script not found: %BACKUP_SCRIPT%
    pause
    exit /b 1
)

echo Creating scheduled task: Daily backup at 02:00 AM
echo.

schtasks /create /tn "BusinessSystem_DailyBackup" /tr "\"%BACKUP_SCRIPT%\"" /sc daily /st 02:00 /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] Scheduled task created successfully!
    echo Task name: BusinessSystem_DailyBackup
    echo Schedule: Daily at 02:00 AM
    echo Backup script: %BACKUP_SCRIPT%
    echo.
    echo To test immediately, run: 备份数据库.bat
    echo To view task: schtasks /query /tn BusinessSystem_DailyBackup
    echo To delete task: schtasks /delete /tn BusinessSystem_DailyBackup /f
) else (
    echo.
    echo [ERROR] Failed to create scheduled task.
    echo Please run this script as Administrator.
)

echo.
pause
