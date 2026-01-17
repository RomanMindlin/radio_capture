#!/bin/bash
# Configure cron based on channels.json and start the application
set -eu

CONFIG_PATH=${CONFIG_PATH:-/app/channels.json}
PYTHON_BIN=${PYTHON_BIN:-python}
CRON_FILE=/etc/cron.d/radio-summary
LOG_TARGET=${CRON_LOG_TARGET:-/proc/1/fd/1}

# Ensure data directories exist (in case volume is empty)
mkdir -p /data/recordings
mkdir -p /data/database

# Run migrations
echo "Running database migrations..."
if [ -f "alembic.ini" ]; then
    alembic upgrade head
else
    echo "Warning: alembic.ini not found, skipping migrations."
fi

# Configure cron if channels.json exists
if [ -f "${CONFIG_PATH}" ]; then
    echo "Configuring cron schedule from ${CONFIG_PATH}..."
    
    # Extract cron schedule from channels.json
    CRON_SCHEDULE=$("${PYTHON_BIN}" - <<'PY' "${CONFIG_PATH}"
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
try:
    data = json.loads(config_path.read_text())
    cron_schedule = data.get("cron", "0 6 * * *")  # Default to 6 AM daily
    print(cron_schedule)
except Exception as exc:
    print(f"Failed to read {config_path}: {exc}", file=sys.stderr)
    sys.exit(1)
PY
    )
    
    if [ -z "${CRON_SCHEDULE}" ]; then
        echo "Warning: No cron schedule found in config, using default (0 6 * * *)"
        CRON_SCHEDULE="0 6 * * *"
    fi
    
    echo "Cron schedule: ${CRON_SCHEDULE}"
    
    # Build the command to run
    COMMAND="cd /app && ${PYTHON_BIN} /app/run_daily_summaries.py --config ${CONFIG_PATH} >> ${LOG_TARGET} 2>&1"
    
    # Create cron file
    cat > "${CRON_FILE}" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PYTHONPATH=/app
DATABASE_URL=${DATABASE_URL:-sqlite:////data/database/radio.db}
OPENAI_API_KEY=${OPENAI_API_KEY}

${CRON_SCHEDULE} root ${COMMAND}
EOF
    
    chmod 0644 "${CRON_FILE}"
    
    echo "Cron configured successfully"
    
    # Start cron daemon in background
    cron
    
    echo "Cron daemon started"
else
    echo "Warning: ${CONFIG_PATH} not found, cron scheduling disabled"
fi

echo "Starting Application..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000
