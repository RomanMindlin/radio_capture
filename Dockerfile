FROM python:3.11-slim

# Install system dependencies
# ffmpeg is required for audio capture
# curl/wget for healthchecks and model downloads
# cron is required for scheduled tasks
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    wget \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories for data volume
# These will be mapped to the external volume, but we ensure they exist in the image structure
RUN mkdir -p /data/recordings /data/database

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONPATH=/app

# Make entrypoint scripts executable
RUN chmod +x start.sh start_watcher.sh

# Expose port
EXPOSE 8000

# Entrypoint
CMD ["./start.sh"]
