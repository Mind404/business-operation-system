@echo off
title Backup Database
set "CUR=%~dp0"
echo Backing up database...
if not exist "%CUR%..\python\python.exe" (
    echo [ERROR] python runtime not found.
    pause
    exit /b 1
)
if not exist "%CUR%..\business.db" (
    echo [ERROR] database not found. Start the system once first.
    pause
    exit /b 1
)
"%CUR%..\python\python.exe" -c "import shutil, os, datetime; src='%CUR%..\business.db'; dstdir='%CUR%..\backups'; os.makedirs(dstdir, exist_ok=True); dst=os.path.join(dstdir, 'backup_' + datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S') + '.db'); shutil.copy2(src, dst); print('Backup saved:', dst)"
echo.
pause
