@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   J.A.R.V.I.S. 环境安装
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.11：
    echo   https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo [1/3] 创建虚拟环境 .venv ...
python -m venv .venv
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败
    pause
    exit /b 1
)

echo [2/3] 安装依赖（清华镜像，含 torch 较大，请耐心等待）...
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo [3/3] 配置 DeepSeek API Key ...
echo 请准备 DeepSeek API Key（免费注册 https://platform.deepseek.com）
set /p KEY=请输入你的 API Key（sk- 开头）:
echo DEEPSEEK_API_KEY=%KEY%> .env
echo DEEPSEEK_BASE_URL=https://api.deepseek.com>> .env
echo DEEPSEEK_MODEL=deepseek-chat>> .env

echo.
echo [提示] ffmpeg 需单独准备：放到本项目下 ffmpeg\bin\ffmpeg.exe，或加入系统 PATH
echo        模型文件（whisper + 语义模型）会在首次运行时自动下载
echo.
echo 安装完成！双击 start.bat 启动，浏览器打开 http://localhost:8000
pause
