@echo off
chcp 65001 >nul
echo 正在停止业务系统...
taskkill /f /im python.exe 2>nul
echo.
echo 系统已停止。
timeout /t 2 /nobreak >nul
