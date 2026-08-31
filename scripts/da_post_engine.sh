#!/usr/bin/env bash
# Cronjob daemon compatibility shim (Vincent, 31-8-2026).
# DE BUG: de Hermes cronjob daemon start `bash run-da.sh` via cygpath-converter
# in de daemon context, maar de MSYS->Win32 path translation in de daemon
# subprocess layer strip backslashes van 'C:\Users\...' → exit 127 (file not found),
# ZONDER dat de wrapper body (set -x / echo / cd) ooit draait. De output die de
# daemon vangt is leeg → last_status=error, last_fire_error=null.
#
# DE FIX: de engine direct via de cronjob daemon LATEN runnen met een
# PYTHON‑script (Windows‑native exe, geen bash path resolution needed).
# Dit bestand is een MINIMALE bash shim die ALLEEN werkt als de daemon bash
# resolveert; de cronjob gebruikt daarna `script: da_engine.py` (direct
# via .venv python — zie cronjob update). Handmatige `bash da_post_engine.sh`
# blijft werken voor lokale test.
WIN_REPO='D:/APPS/agentos'
REPO="$(cygpath -u "$WIN_REPO")"
PYTHON="$(cygpath -w "$REPO/.venv/Scripts/python.exe")"
SCRIPT="$(cygpath -w "$REPO/scripts/da_post_engine.py")"
cd "$REPO" || exit 2
export PYTHONPATH="$REPO"
"$PYTHON" "$SCRIPT"
exit $?
