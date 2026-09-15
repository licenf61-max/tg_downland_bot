@echo off
setlocal
cd /d "%~dp0"
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul ^| findstr /i /v "WindowsApps"') do if not defined PY set "PY=%%i"
)
if not defined PY (
    echo Python not found. Install from https://www.python.org/downloads/ and check "Add to PATH".
    pause
    exit /b 1
)
echo Using: %PY%
"%PY%" telethon_login.py
pause
