#!/bin/bash
set -e

# Ensure data directories exist (in case volume is empty)
mkdir -p /data/recordings
mkdir -p /data/database

# Run migrations
# checking if alembic.ini exists, if not we might need to init (first run logic handled in app startup or here)
# For this setup, we assume alembic is configured. 
# We'll run an upgrade head. If it fails (e.g. first run), we might handle it.
# Ideally, we should wait for DB, but since it's SQLite, it is instant.

echo "Running database migrations..."
# We will create the alembic.ini and migrations folder next, 
# but for the script to be robust we check if they exist.
if [ -f "alembic.ini" ]; then
    alembic upgrade head
else
    echo "Warning: alembic.ini not found, skipping migrations."
fi

echo "Starting Application..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000
