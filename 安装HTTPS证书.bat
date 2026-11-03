@echo off
chcp 65001 >nul
title 安装HTTPS证书（首次使用必做）
set "CUR=%~dp0"

echo ============================================================
echo   安装本系统的 HTTPS 安全证书（首次使用 HTTPS 模式必做）
echo.
echo   作用：把本系统自签证书装入本机「受信任根证书」，
echo        之后浏览器访问 https://127.0.0.1:5000 可直接打开，
echo        不会再超时（ERR_TIMED_OUT），也不会再提示证书不受信任。
echo ============================================================
echo.

if not exist "%CUR%app\cert\server.crt" (
    echo [错误] 未找到证书文件 app\cert\server.crt，请确认是完整解压后再运行。
    echo.
    pause
    exit /b 1
)

echo [检查] 本机是否已安装过本系统证书 ...
certutil -user -store Root business.local >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo   [提示] 证书已经安装过了，无需重复安装。
    echo          直接双击「启动系统-HTTPS.bat」启动即可。
    echo.
    pause
    exit /b 0
)

echo [安装] 正在把证书装入当前账号的受信任根（安全、可随时卸载）...
certutil -addstore -user Root "%CUR%app\cert\server.crt"
if %errorlevel%==0 (
    echo.
    echo   [完成] 证书安装成功！
    echo   接下来：双击「启动系统-HTTPS.bat」启动系统。
    echo.
    echo   注意：本操作只影响当前 Windows 账号。团队其他电脑/账号
    echo        使用 HTTPS 模式时，需要各自运行一次本脚本。
) else (
    echo.
    echo   [失败] 证书安装未成功，请把本窗口截图发给维护方。
)
echo.
pause
