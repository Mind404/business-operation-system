@echo off
title Build Portable Package
echo Building portable package...
powershell Compress-Archive -Path "%~dp0..\*" -DestinationPath "%~dp0..\v12.7.1-portable.zip" -Force
echo Done.
pause
