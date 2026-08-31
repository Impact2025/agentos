@echo off
cd /d /APPS/agentos
set PYTHONPATH=D:\APPS\agentos
.venv\Scripts\python.exe -u scripts\da_post_engine.py > data\logs\da_run_%DATE:~-4,4%%DATE:~-10,2%%DATE:~-7,2%.log 2>&1
set PAUSE
echo EXIT=%ERRORLEVEL% >> data\logs\da_run_...log
