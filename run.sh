#!/usr/bin/env bash
set -euo pipefail

exec "${PYTHON_BIN:-python3}" -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}"
