@echo off
REM ============================================================
REM  Build SparkyBot.exe (one-directory) — run from repo root
REM  Double-click this file on a Windows 10/11 machine with
REM  Python 3.11+ on PATH.
REM
REM  Output: dist\SparkyBot\SparkyBot.exe
REM ============================================================

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
    python -m venv build\venv
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create venv. Make sure Python 3.11+ is installed.
        pause
        exit /b 1
    )
)

echo   Activating venv and installing dependencies...
call build\venv\Scripts\activate.bat

REM --- Step 2: pip install ---
echo.
echo [2/4] Installing packages...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet

if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)

REM --- Step 3: PyInstaller ---
echo.
echo [3/4] Building SparkyBot.exe...
pyinstaller --clean --noconfirm build\sparkybot.spec

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. See the output above.
    pause
    exit /b 1
)

REM --- Step 4: standalone updater (one-file, copied beside SparkyBot.exe) ---
echo.
echo [4/4] Building SparkyBotUpdater.exe...
pyinstaller --clean --noconfirm build\sparkybot_updater.spec

if errorlevel 1 (
    echo.
    echo ERROR: Updater build failed. See the output above.
    pause
    exit /b 1
)

copy /Y dist\SparkyBotUpdater.exe dist\SparkyBot\SparkyBotUpdater.exe >nul

REM --- Copy GW2EI next to the exe (writable, outside _internal) ---
echo.
echo Copying GW2EI to dist\SparkyBot\GW2EI...
if exist dist\SparkyBot\GW2EI rmdir /s /q dist\SparkyBot\GW2EI
if exist GW2EI (
    xcopy GW2EI dist\SparkyBot\GW2EI\ /E /I /Q /H >nul
) else (
    REM GW2EI is intentionally git-ignored. A clean release clone still needs
    REM the writable destination so first-run setup can install Elite Insights.
    mkdir dist\SparkyBot\GW2EI
)

echo.
echo ============================================================
echo  Build complete!
echo  Output: dist\SparkyBot\SparkyBot.exe
echo.
echo  BEFORE DISTRIBUTING, run the smoke checklist in
echo  build\README-BUILD.md on a clean machine.
echo.
echo  To package for release:
echo    zip -r SparkyBot-vX.Y.Z.zip dist\SparkyBot\
echo ============================================================
pause
