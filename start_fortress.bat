@echo off
chcp 65001 >nul 2>&1
title Fortress V2 — AI Daemon

echo ========================================
echo   Fortress V2 — Event-driven AI Daemon
echo ========================================
echo.

cd /d "C:\Users\badge\Desktop\Проект Вдохновение\Fortress"

echo [1/2] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+
    pause
    exit /b 1
)
echo OK

echo [2/2] Starting Fortress...
echo.
echo Dashboard: http://127.0.0.1:8090
echo Press Ctrl+C to stop
echo.

python -m fortress --config config.yaml

pause
