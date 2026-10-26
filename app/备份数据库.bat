@echo off
chcp 65001 >nul
title Database Backup

setlocal
set "APP_DIR=%~dp0"
set "DB_FILE=%APP_DIR%business.db"
set "BACKUP_DIR=%APP_DIR%backups"

if not exist "%DB_FILE%" (
    echo [ERROR] Database file not found: %DB_FILE%
    pause
    exit /b 1
)

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set "DT=%%I"
set "TIMESTAMP=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%_%DT:~8,2%%DT:~10,2%%DT:~12,2%"

set "BACKUP_FILE=%BACKUP_DIR%\business_%TIMESTAMP%.db"

copy /Y "%DB_FILE%" "%BACKUP_FILE%" >nul
if %errorlevel% equ 0 (
    echo [OK] Backup created: %BACKUP_FILE%
) else (
    echo [ERROR] Backup failed!
    pause
    exit /b 1
)

echo.
echo Cleaning old backups (keeping last 7 days)...
forfiles /p "%BACKUP_DIR%" /m "business_*.db" /d -7 /c "cmd /c del @path" 2>nul

echo.
echo Backup completed successfully.
echo Backup directory: %BACKUP_DIR%
echo.
endlocal
