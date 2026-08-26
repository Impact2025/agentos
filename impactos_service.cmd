@echo off
rem Impact OS Service - achtergrond server, geen console venster
cd /d D:\apps\agentos
set "PYTHONIOENCODING=utf-8"

rem NotebookLM MCP-server, onderzoek-agent, HTTP poort 3137
set "NOTEBOOKLM_TRANSPORT=http"
set "NOTEBOOKLM_PORT=3137"
set "NOTEBOOKLM_HOST=127.0.0.1"
set "NOTEBOOKLM_AI_MARKER=false"
set "NOTEBOOKLM_PROFILE=full"

if exist "%APPDATA%\notebooklm-mcp" (
    echo [impactos] Start NotebookLM MCP-server op poort 3137
    start "" /b /min cmd /c "C:\Users\v_mun\AppData\Roaming\npm\notebooklm-mcp.cmd"
    timeout /t 4 >nul
) else (
    echo [impactos] notebooklm-mcp niet gevonden, onderzoek-agent overgeslagen
)

rem FastAPI backend, Impact OS SPA
"D:\apps\agentos\.venv\Scripts\uvicorn.exe" backend.main:app --host localhost --port 1250 >> "D:\apps\agentos\logs\impactos.log" 2>&1
exit /b 0
