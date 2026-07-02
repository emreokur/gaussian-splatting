#!/usr/bin/env bash
# Start the backend (FastAPI, :8000) and frontend (Vite, :5173) together.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x backend/.venv/bin/uvicorn ]; then
  echo "Setting up backend venv…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend deps…"
  (cd frontend && npm install)
fi

trap 'kill 0' EXIT INT TERM

(cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000) &
(cd frontend && npm run dev) &

echo
echo "  App:     http://localhost:5173"
echo "  Backend: http://localhost:8000"
echo
wait
