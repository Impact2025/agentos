@echo off
rem ============================================================
rem  AgentOS (port 1250) herstarten — VOOR KNOWLEDGE FORGE (/api/learn)
rem  Dubbelklik dit bestand OF run: restart_agentos_1250.cmd
rem  (Hermes Agent mag dit niet zelf uitvoeren wegens taskkill-blokkade)
rem ============================================================
cd /d D:\APPS\agentos

echo [1/3] Zoek draaiende AgentOS op poort 1250...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":1250" ^| findstr "LISTENING"') do (
    set "PID=%%P"
)
if not defined PID (
    echo Geen proces op 1250 — start direct.
    goto :start
)

echo [2/3] Stop oud proces (PID %PID%)...
taskkill /PID %PID% /F
timeout /t 3 >nul

:start
echo [3/3] Start AgentOS op 1250...
set "PYTHONIOENCODING=utf-8"
start "" /b "D:\APPS\agentos\.venv\Scripts\uvicorn.exe" backend.main:app --host localhost --port 1250
timeout /t 6 >nul

echo Done. Check: curl http://127.0.0.1:1250/api/learn/documents
pause
