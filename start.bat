@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Audio Studio

rem ---- 找 Python；没有的话尝试用 winget 自动安装
set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py"
if not defined PYTHON_CMD (
    where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD goto :no_python

rem ---- 第一次运行：创建独立环境
if not exist ".venv\Scripts\python.exe" (
    echo [首次运行] 正在创建独立环境，请稍候……
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :install_failed
)
set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"

rem ---- 安装/补齐 Python 组件（yt-dlp、ffmpeg）
"%VENV_PY%" -c "import yt_dlp, imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()" >nul 2>nul
if errorlevel 1 (
    echo 正在安装所需组件（需要联网，几分钟）……
    "%VENV_PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 goto :install_failed
    "%VENV_PY%" -c "import yt_dlp, imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()" >nul 2>nul
    if errorlevel 1 goto :ffmpeg_failed
)

rem ---- 录音组件、AI 组件（已经准备好时会直接跳过）
"%VENV_PY%" setup_tools.py

start "" "%VENV_PYW%" "%~dp0audio_studio.pyw"
exit /b 0

:no_python
echo 没有找到 Python，正在尝试自动安装（需要联网）……
where winget >nul 2>nul
if errorlevel 1 goto :manual_python
winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :manual_python
echo.
echo Python 已安装。请关掉这个窗口，再双击一次 start.bat。
pause
exit /b 0

:manual_python
echo.
echo [!] 自动安装失败。请从 https://www.python.org/downloads/ 安装 Python，
echo     安装时勾选 "Add python.exe to PATH"，然后再次双击 start.bat。
pause
exit /b 1

:install_failed
echo.
echo [!] 安装失败。请检查网络连接，然后重新双击 start.bat。
pause
exit /b 1

:ffmpeg_failed
echo.
echo [!] 当前 Python 版本无法自动安装 Windows 版 ffmpeg。
echo     请改用 python.org 提供的 64 位 Python 3.10 或更高版本后重试。
pause
exit /b 1
