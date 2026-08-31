@echo off
setlocal EnableExtensions
pushd "%~dp0\.."

echo [1/10] Verifying a clean exact release tag before generating outputs...
py -3.12 build\verify_release.py --repo-root . --require-tag --repo-only
if errorlevel 1 goto :fail

echo [2/10] Building the locked PyInstaller runtime...
call build\build_windows.bat --no-pause
if errorlevel 1 goto :fail

for /f "usebackq delims=" %%V in (`build\venv\Scripts\python.exe -c "from core.version import VERSION; print(VERSION)"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
    echo ERROR: Could not read APP_VERSION from core\version.py.
    goto :fail
)

echo [3/10] Running the full suite at the exact release commit...
set "QT_QPA_PLATFORM=offscreen"
build\venv\Scripts\python.exe -m pytest -q
if errorlevel 1 goto :fail

echo [4/10] Signing application executables when a signing command is configured...
if defined SPARKYBOT_SIGN_COMMAND (
    call %SPARKYBOT_SIGN_COMMAND% "dist\SparkyBot\SparkyBot.exe"
    if errorlevel 1 goto :fail
    call %SPARKYBOT_SIGN_COMMAND% "dist\SparkyBot\SparkyBotUpdater.exe"
    if errorlevel 1 goto :fail
) else if /I not "%SPARKYBOT_ALLOW_UNSIGNED_CANDIDATE%"=="1" (
    echo ERROR: SPARKYBOT_SIGN_COMMAND is required for a publishable release.
    goto :fail
)

echo [5/10] Creating the filtered installer tree, deterministic ZIP, and manifest...
build\venv\Scripts\python.exe build\package_release.py ^
    --repo-root . ^
    --dist-dir dist\SparkyBot ^
    --staging-dir dist\release-staging ^
    --output-dir dist\release ^
    --version %APP_VERSION%
if errorlevel 1 goto :fail

echo [6/10] Building the Inno Setup installer from the filtered tree...
set "ISCC_EXE="
for %%I in (iscc.exe) do set "ISCC_EXE=%%~$PATH:I"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE (
    echo ERROR: Inno Setup 6 is not installed or iscc.exe is not on PATH.
    goto :fail
)
"%ISCC_EXE%" /DMyAppVersion=%APP_VERSION% build\sparkybot.iss
if errorlevel 1 goto :fail

echo [7/10] Signing the final installer when a signing command is configured...
if defined SPARKYBOT_SIGN_COMMAND (
    call %SPARKYBOT_SIGN_COMMAND% "dist\release\SparkyBot-v%APP_VERSION%-Setup.exe"
    if errorlevel 1 goto :fail
)

echo [8/10] Verifying Authenticode before final checksums...
set "AUTHENTICODE_MODE="
if /I "%SPARKYBOT_ALLOW_UNSIGNED_CANDIDATE%"=="1" set "AUTHENTICODE_MODE=-AllowUnsigned"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File build\verify_authenticode.ps1 ^
    %AUTHENTICODE_MODE% ^
    dist\SparkyBot\SparkyBot.exe ^
    dist\SparkyBot\SparkyBotUpdater.exe ^
    dist\release\SparkyBot-v%APP_VERSION%-Setup.exe
if errorlevel 1 goto :fail

echo [9/10] Finalizing checksums for every verified release artifact...
build\venv\Scripts\python.exe -m build.finalize_release ^
    --output-dir dist\release ^
    --version %APP_VERSION%
if errorlevel 1 goto :fail

echo [10/10] Verifying source, archive bytes, manifest, and checksums...
build\venv\Scripts\python.exe build\verify_release.py ^
    --repo-root . ^
    --version %APP_VERSION% ^
    --require-tag ^
    --allow-untracked ^
    --archive dist\release\SparkyBot-v%APP_VERSION%.zip ^
    --manifest dist\release\SparkyBot-v%APP_VERSION%.manifest.json ^
    --checksums dist\release\SHA256SUMS ^
    --installer dist\release\SparkyBot-v%APP_VERSION%-Setup.exe
if errorlevel 1 goto :fail

echo PASS: v%APP_VERSION% ZIP, manifest, checksums, and installer are in dist\release\
popd
exit /b 0

:fail
echo FAILED: no release is approved for publishing.
popd
exit /b 1
