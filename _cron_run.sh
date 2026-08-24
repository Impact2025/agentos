#!/usr/bin/env bash
set -e
cd "D:/APPS/impactos"
PW=$(grep -i '^IMPACTOS_PASSWORD=' .env | head -1 | cut -d= -f2-)
CJ="$(pwd)/_cj_tmp.txt"
: > "$CJ"
LOGIN=$(curl -s -c "$CJ" -X POST http://127.0.0.1:1250/api/auth/login -H 'Content-Type: application/json' -d "{\"password\":\"$PW\"}")
echo "LOGIN: $LOGIN"
RESULT=$(curl -s -b "$CJ" -X POST http://127.0.0.1:1250/api/orchestrator/process-one -H 'Content-Type: application/json' -d '{"threshold":80}')
echo "PROCESS-ONE: $RESULT"
rm -f "$CJ"
