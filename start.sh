#!/bin/bash
# Configure cron based on channels.json and start the application
set -eu

CONFIG_PATH=${CONFIG_PATH:-/config/channels.json}
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
        echo "Error: No cron schedule found in ${CONFIG_PATH}. Please add a 'cron' field to the configuration." >&2
        exit 1
    fi
    
    echo "Cron schedule: ${CRON_SCHEDULE}"

    if [ -z "${OPENAI_API_KEY:-}" ]; then
        echo "Warning: OPENAI_API_KEY is empty — daily summaries will fail to generate." >&2
    fi

    # Build the command to run.
    # The marker line makes it possible to tell "cron never fired" apart from
    # "cron fired but the job produced nothing".
    COMMAND="cd /app && echo \"[cron] \$(date -u +%Y-%m-%dT%H:%M:%SZ) firing run_daily_summaries.py\" && ${PYTHON_BIN} /app/run_daily_summaries.py --config ${CONFIG_PATH}; echo \"[cron] \$(date -u +%Y-%m-%dT%H:%M:%SZ) run_daily_summaries.py exited with \$?\""
    COMMAND="{ ${COMMAND} ; } >> ${LOG_TARGET} 2>&1"

    # Create cron file.
    # Cron jobs do NOT inherit the container environment, so every variable the
    # job needs must be written here explicitly — the logging variables used to
    # be missing, which is why the summary run never appeared in /data/logs.
    cat > "${CRON_FILE}" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PYTHONPATH=/app
PYTHONUNBUFFERED=1
PYTHONIOENCODING=utf-8
DATABASE_URL=${DATABASE_URL:-sqlite:////data/database.sqlite}
OPENAI_API_KEY=${OPENAI_API_KEY:-}
ENABLE_RADIO_LOGS=${ENABLE_RADIO_LOGS:-false}
LOG_DIR=${LOG_DIR:-/data/logs}

${CRON_SCHEDULE} root ${COMMAND}
EOF
    
    chmod 0644 "${CRON_FILE}"
    
    echo "Cron configured successfully"
    
    echo "Cron file ${CRON_FILE}:"
    sed 's/^\(OPENAI_API_KEY=\).*/\1<redacted>/' "${CRON_FILE}"

    # Start cron daemon in background
    cron

    # Verify it is actually running — a silently dead cron daemon means no
    # summaries at all, with nothing in the logs to explain it.
    # (python:3.11-slim has no ps/pgrep, so read /proc directly.)
    cron_running=false
    for comm in /proc/[0-9]*/comm; do
        if [ -r "${comm}" ] && [ "$(cat "${comm}" 2>/dev/null)" = "cron" ]; then
            cron_running=true
            break
        fi
    done

    if [ "${cron_running}" = true ]; then
        echo "Cron daemon started and running"
    else
        echo "ERROR: cron daemon is NOT running — daily summaries will never be sent!" >&2
    fi
else
    echo "Warning: ${CONFIG_PATH} not found, cron scheduling disabled"
fi

echo "Starting Application..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000
