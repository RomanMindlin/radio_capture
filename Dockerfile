FROM python:3.11-slim

# Install system dependencies
# ffmpeg is required for audio capture
# curl/wget for healthchecks and model downloads
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    wget \
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

# Make entrypoint executable
RUN chmod +x start.sh

# Expose port
EXPOSE 8000

# Entrypoint
CMD ["./start.sh"]
