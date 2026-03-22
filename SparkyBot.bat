@echo off
setlocal

:: SparkyBot Launcher
:: Uses Python for efficient file watching

title SparkyBot

:: Check for Python
python --version 1>nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found. Please install Python 3.9+ from https://python.org
    echo.
    pause
    exit /b 1
)

:: Set PATH to include bundled tools if needed
set PATH=%~dp0;%PATH%

:: Launch Python watcher
echo Starting SparkyBot...
python "%~dp0bootstrap.py" %*

exit /b %ERRORLEVEL%
