@echo off
REM ============================================================
REM  Build SparkyBot.exe (one-directory) — run from repo root
REM  Double-click this file on a Windows 10/11 machine with
REM  Python 3.11+ on PATH.
REM
REM  Output: dist\SparkyBot\SparkyBot.exe
REM ============================================================

setlocal EnableExtensions
set "NO_PAUSE="
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

echo.
echo ============================================================
echo  SparkyBot build kit
echo ============================================================
echo.

REM --- Step 1: venv ---
echo [1/4] Creating virtual environment...
if exist build\venv (
    echo   Removing old venv...
    rmdir /s /q build\venv
)
py -3.12 -m venv build\venv 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: Python 3.12 is required for the locked Windows build.
    if not defined NO_PAUSE pause
    exit /b 1
)

echo   Activating venv and installing dependencies...
call build\venv\Scripts\activate.bat

REM --- Step 2: pip install ---
echo.
echo [2/4] Installing packages...
python -m pip install -r build\requirements-windows.lock --quiet

if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Check your internet connection.
    if not defined NO_PAUSE pause
    exit /b 1
)

REM --- Step 3: PyInstaller ---
echo.
echo [3/4] Building SparkyBot.exe...
pyinstaller --clean --noconfirm build\sparkybot.spec

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. See the output above.
    if not defined NO_PAUSE pause
    exit /b 1
)

REM --- Step 4: standalone updater (one-file, copied beside SparkyBot.exe) ---
echo.
echo [4/4] Building SparkyBotUpdater.exe...
pyinstaller --clean --noconfirm build\sparkybot_updater.spec

if errorlevel 1 (
    echo.
    echo ERROR: Updater build failed. See the output above.
    if not defined NO_PAUSE pause
    exit /b 1
)

copy /Y dist\SparkyBotUpdater.exe dist\SparkyBot\SparkyBotUpdater.exe >nul

echo.
echo ============================================================
echo  Build complete!
echo  Output: dist\SparkyBot\SparkyBot.exe
echo.
echo  BEFORE DISTRIBUTING, run the smoke checklist in
echo  build\README-BUILD.md on a clean machine.
echo.
echo  Build release artifacts only with:
echo    build\release_windows.bat
echo ============================================================
if not defined NO_PAUSE pause
endlocal
