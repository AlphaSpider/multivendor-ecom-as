#!/bin/sh
set -e

# Wait for PostgreSQL database if configured
if [ "$WAIT_FOR_DB" = "true" ] || [ "$WAIT_FOR_DB" = "1" ]; then
    echo "Waiting for database connection..."
    python - <<'EOF'
import os
import sys
import time
import psycopg

db_url = os.environ.get("DATABASE_URL")
host = os.environ.get("DB_HOST", "db")
port = int(os.environ.get("DB_PORT", 5432))
user = os.environ.get("DB_USER", "postgres")
password = os.environ.get("DB_PASSWORD", "")
dbname = os.environ.get("DB_NAME", "manzilcart")

max_retries = 30
for i in range(1, max_retries + 1):
    try:
        if db_url:
            conn = psycopg.connect(db_url, connect_timeout=3)
        else:
            conn = psycopg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=dbname,
                connect_timeout=3
            )
        conn.close()
        print("Database connected successfully!")
        sys.exit(0)
    except Exception as e:
        print(f"[{i}/{max_retries}] Waiting for database... ({e})")
        time.sleep(1)

print("Error: Database connection timed out.", file=sys.stderr)
sys.exit(1)
EOF
fi

# Apply database migrations if requested
if [ "$AUTO_MIGRATE" = "true" ] || [ "$AUTO_MIGRATE" = "1" ]; then
    echo "Applying database migrations..."
    python manage.py migrate --noinput
fi

# Execute CMD
exec "$@"
