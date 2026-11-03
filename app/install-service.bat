@echo off
title Install as Windows Service
echo Installing Business System as Windows service...
nssm install BusinessSystem "%~dp0..\python\python.exe" "%~dp0app.py"
nssm start BusinessSystem
echo Done.
pause
