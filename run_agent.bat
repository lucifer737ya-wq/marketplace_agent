@echo off
cd /d C:\marketplace_agent
timeout /t 20 /nobreak > nul
call venv\Scripts\python.exe supervisor.py