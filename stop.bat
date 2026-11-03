@echo off
title Stop Business System
echo Stopping Business System...
taskkill /f /im python.exe 2>nul
taskkill /f /im pythonw.exe 2>nul
echo Done.
timeout /t 2 /nobreak >nul
