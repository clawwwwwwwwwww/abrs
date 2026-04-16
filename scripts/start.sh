#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Apply migrations (sqlite default, postgres if DATABASE_URL points there).
python3 -c "import asyncio; from app.db import migrate; asyncio.run(migrate())"

PORT="${PORT:-8000}"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
