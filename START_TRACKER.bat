@echo off
title EchoScope AI - Launcher
color 0C

:: Set current directory to script location
cd /d "%~dp0"

echo ==================================================
echo       ECHOSCOPE AI - YOUTUBE INTELLIGENCE
echo ==================================================
echo.

:: --- 1. DEPENDENCY CHECK ---
if exist "install.lock" (
    echo [OK] Dependencies already installed. Launching...
    goto START_APP
) else (
    echo [!] First run detected.
    echo [!] Installing necessary libraries (This might take a while)...
    echo.
    
    :: Check if Python is installed
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        color 0E
        echo [ERROR] Python not found! Please install Python 3.10+ and try again.
        echo.
        pause
        exit
    )

    :: Install dependencies
    pip install -r requirements.txt
    
    :: If installation fails, pause to show error
    if %errorlevel% neq 0 (
        color 0E
        echo.
        echo [ERROR] Failed to install dependencies. Check your internet connection or Python installation.
        pause
        exit
    )

    :: Create lock file to skip installation next time
    echo Installed on %date% at %time% > install.lock
    echo.
    echo [SUCCESS] All set!
)

:: --- 2. START APPLICATION ---
:START_APP
echo.
echo [1] Opening Dashboard...
echo [2] Press CTRL+C in this window to stop the server.
echo.

streamlit run app.py

:: If Streamlit closes with error, hold screen
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] The program closed unexpectedly.
    pause
)