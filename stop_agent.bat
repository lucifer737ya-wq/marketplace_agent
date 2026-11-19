@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0" || exit /b 1
set "AGENT_ROOT=%~dp0"
echo Останавливаю только процессы marketplace_agent в этой папке...
del /Q "logs\restart_worker.signal" 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = $env:AGENT_ROOT; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and (($_.CommandLine -like ('*' + $root + '*supervisor.py*')) -or ($_.CommandLine -like ('*' + $root + '*worker.py*'))) } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; Write-Host ('Stopped PID ' + $_.ProcessId) } catch { } }"
timeout /t 2 /nobreak >nul
echo Готово. Проверить: tasklist ^| findstr /I "pythonw python"
