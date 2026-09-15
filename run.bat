@echo off
setlocal
cd /d "%~dp0"

set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python venv not found:
    echo     %PY%
    echo Run: pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env not found. Copy .env.example to .env and fill in BOT_TOKEN.
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
