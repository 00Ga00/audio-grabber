@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py"
if not defined PYTHON_CMD (
    where python >nul 2>nul && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD goto :no_python

if not exist ".venv\Scripts\python.exe" if not exist ".venv\bin\python.exe" (
    echo [首次运行] 正在创建独立环境并安装组件，请稍候...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :install_failed
)

if exist ".venv\Scripts\python.exe" (
    set "VENV_PY=.venv\Scripts\python.exe"
    set "VENV_PYW=.venv\Scripts\pythonw.exe"
) else (
    set "VENV_PY=.venv\bin\python.exe"
    set "VENV_PYW=.venv\bin\pythonw.exe"
)

"%VENV_PY%" -c "import yt_dlp, imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()" >nul 2>nul
if errorlevel 1 (
    echo 正在安装所需组件，请稍候...
    "%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :install_failed
)

"%VENV_PY%" -c "import yt_dlp, imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()" >nul 2>nul
if errorlevel 1 goto :ffmpeg_failed

start "" "%VENV_PYW%" "%~dp0audio_grabber.pyw"
exit /b 0

:no_python
echo [!] 没有找到 Python。
echo     请从 https://www.python.org/downloads/ 安装 Python 3.10 或更高版本，
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
