#!/usr/bin/bash
# DA post-engine cron-wrapper — draait de geplande DA-posts.
cd "D:/apps/agentos" || exit 1
export PYTHONPATH=.
.venv/Scripts/python.exe scripts/da_post_engine.py >> data/uploads/da_engine.log 2>&1
