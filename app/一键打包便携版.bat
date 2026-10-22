@echo off
chcp 65001 >nul
title 业务高效运转系统 - 一键打包便携版
echo ============================================================
echo   业务高效运转系统 - 一键打包便携版
echo   自动下载Python运行环境 + 安装依赖 + 生成拷贝即用版本
echo ============================================================
echo.

:: 设置变量
set "PORTABLE_DIR=业务系统便携版"
set "PYTHON_VERSION=3.11.9"
set "PYTHON_EMBED_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip"
set "PYTHON_ZIP=python-embed.zip"
set "APP_DIR=%~dp0"

echo [1/6] 清理旧的便携版目录...
if exist "%PORTABLE_DIR%" rmdir /s /q "%PORTABLE_DIR%"
mkdir "%PORTABLE_DIR%"
mkdir "%PORTABLE_DIR%\python"
mkdir "%PORTABLE_DIR%\app"

echo.
echo [2/6] 下载Python %PYTHON_VERSION% 便携版（约10MB）...
echo 下载地址: %PYTHON_EMBED_URL%
powershell -Command "Invoke-WebRequest -Uri '%PYTHON_EMBED_URL%' -OutFile '%PYTHON_ZIP%' -UseBasicParsing"
if not exist "%PYTHON_ZIP%" (
    echo [错误] Python下载失败！请检查网络连接。
    pause
    exit /b 1
)
echo 下载完成！

echo.
echo [3/6] 解压Python便携版...
powershell -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PORTABLE_DIR%\python' -Force"
del "%PYTHON_ZIP%"

:: 配置Python embeddable支持pip
echo.
echo [4/6] 配置pip和运行环境...
set "PYTHON_EXE=%PORTABLE_DIR%\python\python.exe"
set "PYTHON_PTH=%PORTABLE_DIR%\python\python311._pth"

:: 启用site-packages（修改._pth文件）
powershell -Command "(Get-Content '%PYTHON_PTH%') -replace '#import site', 'import site' | Set-Content '%PYTHON_PTH%'"

:: 下载get-pip.py并安装pip
powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'get-pip.py' -UseBasicParsing"
"%PYTHON_EXE%" get-pip.py --no-warn-script-location
del get-pip.py

:: 安装应用依赖
echo.
echo [5/6] 安装应用依赖（Flask, waitress等）...
"%PYTHON_EXE%" -m pip install --no-warn-script-location Flask==3.0.0 Werkzeug==3.0.1 Jinja2==3.1.2 itsdangerous==2.1.2 click==8.1.7 blinker==1.7.0 waitress==2.1.2 requests==2.31.0 openpyxl==3.1.2

:: 复制应用代码
echo.
echo [6/6] 复制应用代码和配置...
xcopy "%APP_DIR%templates" "%PORTABLE_DIR%\app\templates\" /E /I /Y >nul
xcopy "%APP_DIR%static" "%PORTABLE_DIR%\app\static\" /E /I /Y >nul 2>&1
copy "%APP_DIR%app.py" "%PORTABLE_DIR%\app\app.py" /Y >nul
copy "%APP_DIR%config.py" "%PORTABLE_DIR%\app\config.py" /Y >nul 2>&1
copy "%APP_DIR%schema.sql" "%PORTABLE_DIR%\app\schema.sql" /Y >nul 2>&1
copy "%APP_DIR%requirements.txt" "%PORTABLE_DIR%\app\requirements.txt" /Y >nul 2>&1

:: 创建启动脚本
(
echo @echo off
echo chcp 65001 ^>nul
echo title 业务高效运转系统
echo cd /d "%%~dp0app"
echo echo ============================================================
echo echo   业务高效运转系统（便携版）
echo echo   正在启动，请稍候...
echo echo ============================================================
echo echo.
echo "%%~dp0python\python.exe" app.py --port 5000
echo echo.
echo echo 系统已停止运行。
echo pause
) > "%PORTABLE_DIR%\启动系统.bat"

:: 创建停止脚本
(
echo @echo off
echo echo 正在停止业务系统...
echo taskkill /f /im python.exe 2^>nul
echo echo 系统已停止。
echo timeout /t 2 /nobreak ^>nul
) > "%PORTABLE_DIR%\停止系统.bat"

:: 创建使用说明
(
echo ============================================================
echo   业务高效运转系统 - 便携版使用说明
echo ============================================================
echo.
echo 【特点】
echo - 拷贝即用，无需安装Python或任何依赖
echo - 双击"启动系统.bat"即可运行
echo - 数据保存在 app\business.db 文件中
echo.
echo 【使用方法】
echo 1. 双击 "启动系统.bat" 启动系统
echo 2. 等待出现 "Running on http://127.0.0.1:5000" 提示
echo 3. 打开浏览器访问 http://127.0.0.1:5000
echo 4. 默认账号：admin / admin123（管理组）
echo 5. 使用完毕后关闭命令行窗口，或双击"停止系统.bat"
echo.
echo 【端口修改】
echo 编辑 app\config.py 文件，修改 PORT 字段即可更改端口
echo 或在启动时使用命令：启动系统.bat --port 6000
echo.
echo 【数据备份】
echo 定期复制 app\business.db 文件即可完成数据备份
echo.
echo 【内网访问】
echo 系统启动后会显示内网访问地址，如 http://192.168.1.100:5000
echo 同一局域网内的其他电脑可通过该地址访问
echo.
echo ============================================================
) > "%PORTABLE_DIR%\使用说明.txt"

echo.
echo ============================================================
echo   ✅ 便携版打包完成！
echo ============================================================
echo.
echo 便携版目录: %PORTABLE_DIR%
echo.
echo 目录结构:
echo   %PORTABLE_DIR%\
echo   ├── python\          (Python运行环境，约30MB)
echo   ├── app\             (应用代码和数据)
echo   │   ├── app.py
echo   │   ├── templates\
echo   │   ├── static\
echo   │   └── business.db  (运行后自动生成)
echo   ├── 启动系统.bat     (双击启动)
echo   ├── 停止系统.bat     (双击停止)
echo   └── 使用说明.txt
echo.
echo 将整个 "%PORTABLE_DIR%" 文件夹拷贝到任何Windows电脑即可使用！
echo.
pause
