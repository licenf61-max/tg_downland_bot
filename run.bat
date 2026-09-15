@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Telegram File Bot - launcher
echo   Dir: %CD%
echo ==========================================
echo.

rem =====================================================================
rem  Step 1: locate a usable Python 3
rem  Keep this file pure ASCII + CRLF, otherwise cmd.exe misparses it.
rem =====================================================================
set "PY="
set "SKIP_ENV="

rem --- 1a. an already-provisioned environment (fast path, no download) ---
set WBENV="%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if exist %WBENV% (
    %WBENV% -c "import telegram" >nul 2>nul
    if not errorlevel 1 (
        set "PY=%WBENV%"
        set "SKIP_ENV=1"
        echo [OK] Reusing an existing ready-to-use environment.
    )
)

rem --- 1b. the py launcher ---
if not defined PY (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)

rem --- 1c. python / python3 on PATH, but skip the Microsoft Store stub ---
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        echo %%i|findstr /i /c:"WindowsApps" >nul
        if errorlevel 1 if not defined PY set PY="%%i"
    )
)
if not defined PY (
    for /f "delims=" %%i in ('where python3 2^>nul') do (
        echo %%i|findstr /i /c:"WindowsApps" >nul
        if errorlevel 1 if not defined PY set PY="%%i"
    )
)

rem --- 1d. common install locations ---
if not defined PY (
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
        if not defined PY if exist "%%d\python.exe" set PY="%%d\python.exe"
    )
)
if not defined PY (
    for /d %%d in ("%ProgramFiles%\Python*") do (
        if not defined PY if exist "%%d\python.exe" set PY="%%d\python.exe"
    )
)
if not defined PY (
    for /d %%d in ("C:\Python*") do (
        if not defined PY if exist "%%d\python.exe" set PY="%%d\python.exe"
    )
)
if not defined PY (
    for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
        if not defined PY if exist "%%d\python.exe" set PY="%%d\python.exe"
    )
)

rem --- 1e. make sure the candidate really runs ---
if defined PY (
    %PY% -c "import sys" >nul 2>nul
    if errorlevel 1 set "PY="
)

if not defined PY (
    echo [ERROR] No usable Python 3 found on this computer.
    echo.
    echo   How to fix:
    echo     1^) Download Python 3.11+ from https://www.python.org/downloads/
    echo     2^) In the installer, TICK "Add python.exe to PATH", then Install Now
    echo     3^) Close this window and double-click run.bat again
    echo.
    echo   Note: if typing "python" opens the Microsoft Store, you only have
    echo   the Store placeholder - install the real thing from python.org.
    echo.
    pause
    exit /b 1
)
echo [OK] Python: %PY%
echo.

if not defined SKIP_ENV (
    rem =================================================================
    rem  Step 2: virtual environment
    rem =================================================================
    if not exist ".venv\Scripts\python.exe" (
        echo [SETUP] Creating virtual environment ^(first run only^) ...
        %PY% -m venv .venv
        if errorlevel 1 (
            echo [ERROR] Could not create .venv
            pause
            exit /b 1
        )
    )
    set PY=".venv\Scripts\python.exe"

    rem =================================================================
    rem  Step 3: dependencies
    rem =================================================================
    if not exist ".venv\Lib\site-packages\telegram" (
        echo [SETUP] Installing dependencies ^(first run only, about 15 MB^) ...
        %PY% -m pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        if errorlevel 1 (
            echo [WARN] Mirror failed, retrying with the default index ...
            %PY% -m pip install -q -r requirements.txt
            if errorlevel 1 (
                echo [ERROR] pip install failed. Check network / proxy settings.
                pause
                exit /b 1
            )
        )
    )
)

rem =====================================================================
rem  Step 4: config file
rem =====================================================================
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [SETUP] Created .env from .env.example.
        echo [SETUP] Fill in BOT_TOKEN and PROXY_URL, save, then run run.bat again.
        start "" notepad ".env"
    ) else (
        echo [ERROR] .env not found. Create it with BOT_TOKEN inside.
    )
    echo.
    pause
    exit /b 1
)

rem =====================================================================
rem  Step 5: run
rem =====================================================================
echo ------------------------------------------
echo   Starting the bot ... press Ctrl+C to stop
echo ------------------------------------------
echo.
%PY% bot.py
echo.
echo Bot stopped.
pause
