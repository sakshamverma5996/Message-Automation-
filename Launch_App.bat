@echo off
title WhatsApp Bulk Messenger - Windows App
cd /d "%~dp0"

if not exist ".\venv\Scripts\pythonw.exe" (
    echo [ERROR] Virtual environment 'venv' not found!
    echo Please make sure the venv directory exists.
    pause
    exit /b 1
)

:: Launch purely as native Windows GUI (No black terminal window)
start "" ".\venv\Scripts\pythonw.exe" app.py
exit

