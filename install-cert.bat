@echo off
title Install HTTPS Certificate
echo Installing local HTTPS certificate...
echo.
certutil -addstore -f Root "%~dp0app\cert\server.crt"
echo.
echo Done. You can now use HTTPS mode.
pause
