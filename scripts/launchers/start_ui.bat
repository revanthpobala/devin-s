@echo off
title Stock Trading Operations Cockpit UI
cd /d "%~dp0\..\.."
python run_ui.py --port 8050
pause
