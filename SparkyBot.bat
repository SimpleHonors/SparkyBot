@echo off
title SparkyBot
echo Starting SparkyBot...

python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.9+ from https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

python bootstrap.py %*
if errorlevel 1 pause
