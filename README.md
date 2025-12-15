# Radio Stream Capture Service

A web-based service for capturing and managing radio stream recordings with automated scheduling, monitoring, and file management capabilities.

## Features

- **Stream Management**: Configure and manage multiple radio streams
- **Automated Recording**: Continuous capture with configurable segment duration
- **Web Dashboard**: Modern UI for monitoring stream status and statistics
- **User Management**: Role-based access control (Admin/Operator)
- **Statistics & Analytics**: Detailed recording metrics with Chart.js visualizations
- **File Management**: Browse, filter, download, and export recordings
- **Retention Policy**: Automatic cleanup removes recordings older than 3 days (metadata kept for audit)
- **Telegram Notifications**: Optional alerts for stream errors and daily reports
- **Database Migrations**: Alembic-based schema versioning

## Technology Stack

- **Backend**: FastAPI, SQLModel, SQLAlchemy
- **Frontend**: Bootstrap 5, Chart.js, Vanilla JavaScript
- **Database**: SQLite (persistent volume)
- **Audio Processing**: FFmpeg
- **Authentication**: JWT with bcrypt password hashing
- **Containerization**: Docker

## Quick Start

### Prerequisites

- Docker
- Docker Compose (optional)

### Installation

1. **Clone the repository**
```bash
git clone <repository-url>
cd radio-stream-capture-service
```

2. **Build the Docker image**
```bash
docker build -t radio-capture .
```

3. **Run the container**
```bash
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  --name radio-service \
  radio-capture
```

4. **Access the application**
- Open your browser to `http://localhost:8000`
- Default credentials: `admin` / `admin`
- **Important**: Change the default password immediately

## Configuration

### Environment Variables

- `SECRET_KEY`: JWT secret key (default: auto-generated, set in production)
- `DATABASE_URL`: SQLite database path (default: `sqlite:////data/database.sqlite`)
- `DATA_DIR`: Data directory path (default: `/data`)

### Stream Configuration

Streams are configured via the web UI with the following parameters:

**Mandatory Parameters:**
- `format`: Output format (e.g., `mp3`, `wav`)
- `segment_time`: Recording segment duration in seconds
- `channels`: Audio channels (1 for mono, 2 for stereo)

**Optional Parameters:**
- `bitrate`: Audio bitrate (e.g., `128k`)
- `retention_days`: Days to keep recordings (default: 30)
- `retry_delay`: Seconds to wait before retry on error

### Recording Retention

Recordings older than **3 days** are automatically purged from disk by the background watcher. Their database entries remain for historical/statistical use but are marked with a `deleted` status so they no longer appear in file listings or downloads. This keeps storage healthy without losing high-level metadata.

## Project Structure

```
radio-stream-capture-service/
├── app/
│   ├── api/              # API routes
│   │   ├── auth.py       # Authentication endpoints
│   │   ├── main.py       # FastAPI application
│   │   ├── recordings.py # Recording management
│   │   ├── stats_routes.py # Statistics API
│   │   ├── streams.py    # Stream management
│   │   ├── ui_routes.py  # Web UI routes
│   │   └── users.py      # User management
│   ├── core/             # Core utilities
│   │   └── db.py         # Database configuration
│   ├── models/           # Data models
│   │   └── models.py     # SQLModel definitions
│   ├── services/         # Business logic
│   │   ├── ffmpeg_builder.py  # FFmpeg command builder
│   │   ├── stats.py      # Statistics aggregation
│   │   ├── stream_manager.py  # Stream lifecycle management
│   │   ├── telegram.py   # Telegram notifications
│   │   └── watcher.py    # File system watcher
│   ├── static/           # Static assets
│   └── templates/        # Jinja2 templates
├── alembic/              # Database migrations
├── data/                 # Persistent data (mounted volume)
│   ├── database/         # SQLite database
│   └── recordings/       # Audio files
├── Dockerfile
├── requirements.txt
└── start.sh
```

## API Documentation

Once running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Key Endpoints

**Authentication:**
- `POST /api/auth/login` - Login and receive JWT token
- `POST /api/auth/logout` - Logout

**Streams:**
- `GET /api/streams` - List all streams
- `POST /api/streams` - Create new stream (Admin only)
- `PUT /api/streams/{id}` - Update stream (Admin only)
- `POST /api/streams/{id}/start` - Start recording
- `POST /api/streams/{id}/stop` - Stop recording

**Statistics:**
- `GET /api/stats/summary` - Aggregated statistics
- `GET /api/stats/files` - List recordings with filters
- `GET /api/stats/files/export` - Export to CSV
- `GET /api/stats/files/{id}/download` - Download recording

## Development

### Local Development Setup

1. **Install dependencies**
```bash
pip install -r requirements.txt
```

2. **Run database migrations**
```bash
alembic upgrade head
```

3. **Start the development server**
```bash
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

### Code Quality

The project follows Python best practices:
- PEP 8 style guide
- Import organization with `isort`
- Type hints with SQLModel/Pydantic

## Database Migrations

Create a new migration:
```bash
alembic revision --autogenerate -m "Description of changes"
```

Apply migrations:
```bash
alembic upgrade head
```

## Monitoring & Logs

View container logs:
```bash
docker logs -f radio-service
```

Access container shell:
```bash
docker exec -it radio-service /bin/bash
```

## Backup & Restore

### Backup
```bash
# Backup database and recordings
docker cp radio-service:/data ./backup-$(date +%Y%m%d)
```

### Restore
```bash
# Restore from backup
docker cp ./backup-20231212/database radio-service:/data/
docker cp ./backup-20231212/recordings radio-service:/data/
docker restart radio-service
```

## Security Considerations

1. **Change default credentials** immediately after first login
2. **Set a strong SECRET_KEY** environment variable in production
3. **Use HTTPS** in production (configure reverse proxy)
4. **Restrict file access** - recordings are served through authenticated endpoints
5. **Regular backups** of the `/data` volume

## Troubleshooting

### Container won't start
```bash
# Check logs
docker logs radio-service

# Verify volume permissions
ls -la data/
```

### FFmpeg errors
- Ensure the stream URL is accessible
- Check FFmpeg parameters in stream configuration
- Verify network connectivity from container

### Database locked errors
- SQLite doesn't handle high concurrency well
- Consider PostgreSQL for production with multiple users

## License

[Specify your license here]

## Contributing

[Add contribution guidelines if applicable]

## Support

[Add support contact information]
