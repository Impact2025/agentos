#!/usr/bin/env bash
COOKIE_FILE="D:/apps/impactos/_iris_cookies.txt"
curl -s -X POST http://127.0.0.1:1250/api/orchestrator/process-one \
  -H 'Content-Type: application/json' \
  -b "$COOKIE_FILE" \
  -d '{"threshold":80}'
echo
