@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
"venv\Scripts\python.exe" calibrate_review_steps.py
pause
