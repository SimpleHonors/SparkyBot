@echo off
setlocal EnableExtensions
pushd "%~dp0\.."

echo [1/6] Building the locked PyInstaller runtime...
call build\build_windows.bat --no-pause
if errorlevel 1 goto :fail

for /f "usebackq delims=" %%V in (`build\venv\Scripts\python.exe -c "from core.version import VERSION; print(VERSION)"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
    echo ERROR: Could not read APP_VERSION from core\version.py.
    goto :fail
)

echo [2/6] Running the full suite at the exact release commit...
set "QT_QPA_PLATFORM=offscreen"
build\venv\Scripts\python.exe -m pytest -q
if errorlevel 1 goto :fail

echo [3/6] Creating deterministic ZIP and manifest for v%APP_VERSION%...
build\venv\Scripts\python.exe build\package_release.py ^
    --repo-root . ^
    --dist-dir dist\SparkyBot ^
    --output-dir dist\release ^
    --version %APP_VERSION%
if errorlevel 1 goto :fail

echo [4/6] Building the Inno Setup installer...
where iscc >nul 2>nul
if errorlevel 1 (
    echo ERROR: Inno Setup 6 is not installed or iscc.exe is not on PATH.
    goto :fail
)
iscc /DMyAppVersion=%APP_VERSION% build\sparkybot.iss
if errorlevel 1 goto :fail

echo [5/6] Verifying the tag, archive bytes, and release manifest...
build\venv\Scripts\python.exe build\verify_release.py ^
    --repo-root . ^
    --version %APP_VERSION% ^
    --require-tag ^
    --archive dist\release\SparkyBot-v%APP_VERSION%.zip ^
    --manifest dist\release\SparkyBot-v%APP_VERSION%.manifest.json
if errorlevel 1 goto :fail

echo [6/6] Verifying Authenticode signatures...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File build\verify_authenticode.ps1 ^
    dist\SparkyBot\SparkyBot.exe ^
    dist\SparkyBot\SparkyBotUpdater.exe ^
    dist\release\SparkyBot-v%APP_VERSION%-Setup.exe
if errorlevel 1 (
    if /I "%SPARKYBOT_ALLOW_UNSIGNED_CANDIDATE%"=="1" (
        echo WARNING: UNSIGNED INTERNAL CANDIDATE ONLY. DO NOT PUBLISH.
    ) else (
        echo ERROR: Release artifacts are unsigned. Publishing is blocked.
        echo        For an internal test build only, set
        echo        SPARKYBOT_ALLOW_UNSIGNED_CANDIDATE=1 and rerun.
        goto :fail
    )
)

echo PASS: v%APP_VERSION% ZIP, manifest, checksums, and installer are in dist\release\
popd
exit /b 0

:fail
echo FAILED: no release is approved for publishing.
popd
exit /b 1
