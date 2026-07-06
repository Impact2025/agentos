@echo off
rem Agent OS Service - achtergrond server (geen console venster)
cd /d D:\apps\agentos
set "PYTHONIOENCODING=utf-8"
"D:\apps\agentos\.venv\Scripts\uvicorn.exe" backend.main:app --host localhost --port 1250 >> "D:\apps\agentos\agentos.log" 2>&1
exit /b 0
