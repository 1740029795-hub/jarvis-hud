@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 J.A.R.V.I.S. ...
start "Jarvis 后端" .venv\Scripts\python.exe server.py
start "Jarvis 前端" python -m http.server 8000
echo.
echo  后端  http://127.0.0.1:8001
echo  前端  http://localhost:8000
echo.
echo 浏览器打开 http://localhost:8000 即可。
pause
