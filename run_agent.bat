@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0" || exit /b 1
if not exist "%~dp0venv\Scripts\python.exe" (
    echo [ERROR] Не найдено виртуальное окружение venv.
    echo Запустите один раз setup_env.bat - он создаст venv и поставит зависимости.
    pause
    exit /b 1
)
"%~dp0venv\Scripts\python.exe" "%~dp0supervisor.py"
exit /b %errorlevel%