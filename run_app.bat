@echo off
REM One-click launcher for the Tripwire app on Windows. Double-click this file.
REM First run: creates .venv, installs the app, builds the web UI. Later runs
REM start straight away. Opens http://127.0.0.1:8765 in your browser.
REM   run_app.bat --demo        force demo mode for this run
REM   run_app.bat --rebuild-ui  rebuild the web UI before starting

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.11 or newer from https://python.org
    echo and tick "Add python to PATH" during installation.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 ( echo Failed to create .venv & pause & exit /b 1 )
) else (
    echo [1/3] Virtual environment already exists.
)

set PY=.venv\Scripts\python.exe

echo [2/3] Installing the app (first run only takes a minute)...
%PY% -m pip install --quiet --upgrade pip
%PY% -m pip install --quiet -e ".[app]"
if errorlevel 1 ( echo Install failed & pause & exit /b 1 )

set REBUILD=0
if "%~1"=="--rebuild-ui" ( set REBUILD=1 & shift )
if not exist "web\out\index.html" set REBUILD=1

if "%REBUILD%"=="1" (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo [3/3] Node.js not found - skipping the UI build. Install Node 20+ from https://nodejs.org
        echo       The app still starts; the page will tell you how to build the UI.
    ) else (
        echo [3/3] Building the web UI...
        pushd web
        call npm install --no-audit --no-fund
        call npm run build
        popd
    )
) else (
    echo [3/3] Web UI already built.
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example - fill it in when you connect accounts. See docs\CONNECT.md
)

echo.
%PY% -m packages.app %1 %2 %3
pause
