#!/bin/bash
set -e

# Ensure data directories exist (in case volume is empty)
mkdir -p /data/recordings
mkdir -p /data/database

# Wait a bit for the main API service to initialize the database
echo "Waiting for database initialization..."
sleep 5

echo "Starting Recording Watcher Process..."
exec python -m app.run_watcher
