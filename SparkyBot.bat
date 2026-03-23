@echo off
setlocal enabledelayedexpansion
:: SparkyBot Launcher
:: Check config for hidden console preference
set HIDDEN=0
for /f "tokens=*" %%A in ('findstr /i "hideconsole" config.properties 2^>nul') do (
    echo %%A | findstr /i "True" >nul 2>&1
    if not errorlevel 1 set HIDDEN=1
)

if %HIDDEN%==1 (
    start "" pythonw bootstrap.py
) else (
    python bootstrap.py
    if !errorlevel! neq 0 pause
)