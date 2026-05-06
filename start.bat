@echo off
chcp 65001 >nul
title Human-API Server

echo ================================================
echo   Human-API Launcher (Windows)
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
pip install -r requirements.txt >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    echo Please run manually: pip install -r requirements.txt
    pause
    exit /b 1
)
echo       OK

echo [2/3] Starting server...
echo.
python app.py

pause
