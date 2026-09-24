@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "VENV_PY=.venv\Scripts\python.exe"
) else if exist ".venv\bin\python.exe" (
    set "VENV_PY=.venv\bin\python.exe"
) else (
    echo 请先运行 start.bat 完成首次安装。
    pause
    exit /b 1
)

echo 正在更新 yt-dlp 和 ffmpeg 组件...
"%VENV_PY%" -m pip install --disable-pip-version-check -U -r requirements.txt
if errorlevel 1 (
    echo [!] 更新失败，请检查网络后重试。
    pause
    exit /b 1
)
echo 更新完成。
pause
