@echo off
title Setup Auto Start
echo Adding to startup...
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET=%~dp0..\start.bat"
mklink "%STARTUP%\BusinessSystem.lnk" "%TARGET%" 2>nul
echo Done.
pause
