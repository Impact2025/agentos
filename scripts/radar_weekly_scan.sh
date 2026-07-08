#!/usr/bin/env bash
# Mission Radar — wekelijkse sky-scan wrapper (draait via Hermes cron).
# Gebruikt de project-venv zodat dotenv/sqlite/deps beschikbaar zijn.
set -e
cd "/d/apps/agentos"
exec ".venv/Scripts/python" "scripts/radar_weekly_scan.py"
