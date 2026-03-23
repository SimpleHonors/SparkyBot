@echo off
:: SparkyBot Launcher
:: Default to showing console
set HIDDEN=0

:: Check if config says to hide console
for /f "tokens=*" %%A in ('findstr /i "hideconsole" config.properties 2^>nul') do (
    echo %%A | findstr /i "True" >nul 2>&1
    if not errorlevel 1 set HIDDEN=1
)

if %HIDDEN%==1 (
    start "" pythonw bootstrap.py
) else (
    python bootstrap.py
    pause
)
