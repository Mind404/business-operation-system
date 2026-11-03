@echo off
title Setup Daily Backup
echo Creating daily backup task...
schtasks /create /tn "BusinessSystemBackup" /tr "%~dp0backup-db.bat" /sc daily /st 23:00 /f
echo Done.
pause
