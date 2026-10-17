#!/bin/sh
# Apply database migrations, then start the app.
set -e
python - <<'PY'
from sqlalchemy import inspect
from app.db import get_engine
insp = inspect(get_engine())
tables = set(insp.get_table_names())
import subprocess
if tables and "alembic_version" not in tables:
    # Database was created by the dev server (create_all) - mark it as current.
    subprocess.run(["alembic", "stamp", "head"], check=True)
subprocess.run(["alembic", "upgrade", "head"], check=True)
PY
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*" --no-access-log
