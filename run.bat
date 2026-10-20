@echo off
title WhatsApp Bulk Messenger - Windows Desktop App
cd /d "%~dp0"

if not exist ".\venv\Scripts\pythonw.exe" (
    echo [ERROR] Virtual environment 'venv' not found!
    pause
    exit /b 1
)

start "" ".\venv\Scripts\pythonw.exe" app.py
exit
