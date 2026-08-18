@echo off
rem Non-interactive restart variant (geen pause, log naar bestand)
cd /d D:\APPS\agentos

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":1250" ^| findstr "LISTENING"') do (
    set "PID=%%P"
)
if not defined PID (
    echo Geen proces op 1250 — start direct.
    goto :start
)

echo Stop oud proces (PID %PID%)...
taskkill /PID %PID% /F
timeout /t 3 >nul

:start
echo Start AgentOS op 1250...
set "PYTHONIOENCODING=utf-8"
start "" /b "D:\APPS\agentos\.venv\Scripts\uvicorn.exe" backend.main:app --host localhost --port 1250 >> "D:\APPS\agentos\agentos_restart.log" 2>&1
echo Gestart.
