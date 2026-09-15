@echo off
setlocal
cd /d "%~dp0"

rem ===== 1. look for python =====
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo [ERROR] Python not found.
    echo Please install Python 3.11 or newer from https://www.python.org/downloads/
    echo IMPORTANT: check "Add python.exe to PATH" during install!
    echo.
    pause
    exit /b 1
)

rem ===== 2. create venv on first run =====
if not exist ".venv\Scripts\python.exe" (
    echo First run: creating virtual environment ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)
set "PY=.venv\Scripts\python.exe"

rem ===== 3. install dependencies if missing =====
if not exist ".venv\Lib\site-packages\telegram" (
    echo Installing dependencies, please wait ...
    "%PY%" -m pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check your network.
        pause
        exit /b 1
    )
)

rem ===== 4. check config =====
if not exist ".env" (
    echo [ERROR] .env not found.
    echo Copy .env.example to .env, then fill in BOT_TOKEN.
    echo.
    pause
    exit /b 1
)

echo ==========================================
echo  Telegram File Bot
echo  Dir: %CD%
echo ==========================================
echo.

"%PY%" bot.py

echo.
echo Bot stopped.
pause
