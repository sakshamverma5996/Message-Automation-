@echo off
title WhatsApp Bulk Messenger (CLI Mode)
cd /d "%~dp0"
call ".\venv\Scripts\activate.bat"
python send_bulk_messages.py
pause
