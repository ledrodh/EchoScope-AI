@echo off
title EchoScope AI - Launcher
color 0C

:: Set current directory to script location
cd /d "%~dp0"

echo ==================================================
echo       ECHOSCOPE AI - YOUTUBE INTELLIGENCE
echo ==================================================
echo.

:: --- 1. VIRTUAL ENVIRONMENT & DEPENDENCIES ---
if exist ".venv\Scripts\streamlit.exe" (
    echo [OK] Virtual environment detected. Launching...
    goto START_APP
) else (
    echo [!] First run or incomplete virtual environment detected.
    echo [!] Setting up Python environment and installing EchoScope AI...
    echo.

    :: Check if Python is installed
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        color 0E
        echo [ERROR] Python not found! Please install Python 3.12+ and try again.
        echo.
        pause
        exit /b 1
    )

    :: Create virtual environment if missing
    if not exist ".venv\Scripts\python.exe" (
        echo [*] Creating virtual environment in .venv...
        python -m venv .venv
        if %errorlevel% neq 0 (
            color 0E
            echo [ERROR] Failed to create virtual environment.
            pause
            exit /b 1
        )
    )

    :: Upgrade pip and install locked dependencies + package in editable mode
    echo [*] Installing locked dependencies and EchoScope AI package...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\pip.exe" install -r requirements.lock
    if %errorlevel% neq 0 (
        color 0E
        echo.
        echo [ERROR] Failed to install locked dependencies from requirements.lock.
        pause
        exit /b 1
    )
    ".venv\Scripts\pip.exe" install --no-deps -e .

    if %errorlevel% neq 0 (
        color 0E
        echo.
        echo [ERROR] Failed to install dependencies. Check your internet connection or Python 3.12+ installation.
        pause
        exit /b 1
    )

    echo.
    echo [SUCCESS] Environment ready!
)

:: --- 2. START APPLICATION ---
:START_APP
echo.
echo [1] Opening Dashboard...
echo [2] Press CTRL+C in this window to stop the server.
echo.

".venv\Scripts\streamlit.exe" run app.py

:: If Streamlit closes with error, hold screen
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] The program closed unexpectedly.
    pause
)
