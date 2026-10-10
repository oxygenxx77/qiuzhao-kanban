@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ═══════════════════════════════════════
echo   🍂 秋招手帐 · Windows 一键安装
echo ═══════════════════════════════════════
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未检测到 Python
    echo.
    echo 请先安装 Python：
    echo   1. 打开 https://www.python.org/downloads/
    echo   2. 下载 Windows 安装包
    echo   3. ⚠️ 安装时务必勾选 "Add Python to PATH"
    echo   4. 装好后重新双击本脚本
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b
)

for /f "tokens=*" %%i in ('python --version') do set PYVER=%%i
echo ✅ 检测到 %PYVER%
echo.

if not exist "venv" (
    echo 📦 正在创建虚拟环境（约 10 秒）...
    python -m venv venv
)
echo ✅ 虚拟环境就绪
echo.

call venv\Scripts\activate.bat

echo 📥 正在安装依赖（首次约 1-2 分钟，请耐心等待）...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
echo ✅ 依赖安装完成
echo.

if not exist ".env" (
    (
        echo # 如需启用 AI 功能，在下面填入你的 Key
        echo # OPENAI_API_KEY=
        echo # OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
        echo # TAVILY_API_KEY=
    ) > .env
    echo ✅ 已创建配置文件 .env
)
echo.

(
    echo @echo off
    echo chcp 65001 ^>nul
    echo cd /d "%%~dp0"
    echo call venv\Scripts\activate.bat
    echo echo.
    echo echo 🍂 正在启动秋招手帐...
    echo echo.
    echo echo 浏览器将自动打开，如未打开请手动访问：http://127.0.0.1:8000
    echo echo.
    echo echo ⚠️ 关闭看板：在本窗口按 Ctrl + C，或直接关掉窗口
    echo echo.
    echo start http://127.0.0.1:8000
    echo uvicorn main:app --reload
    echo pause
) > "启动秋招手帐.bat"
echo ✅ 已创建启动脚本「启动秋招手帐.bat」
echo.

echo ═══════════════════════════════════════
echo   🎉 安装完成！
echo ═══════════════════════════════════════
echo.
echo 以后使用步骤：
echo   1. 双击本文件夹里的「启动秋招手帐.bat」
echo   2. 浏览器会自动打开
echo   3. 关闭时在窗口按 Ctrl + C
echo.

pause