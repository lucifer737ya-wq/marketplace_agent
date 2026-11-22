@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
"venv\Scripts\python.exe" login_ozon.py
pause
