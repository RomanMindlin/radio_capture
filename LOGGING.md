# Logging System Overview

## Troubleshooting: "the bot stopped posting"

The daily Telegram post only happens when a chain of five stages all succeed.
Each stage now logs enough to tell which one broke:

| Stage | Where to look | What proves it worked |
|---|---|---|
| 1. Capture (ffmpeg writes segments) | `stream_<id>_<name>.log`, `radio_capture.api.log` | segment lines, no "Stall detected" |
| 2. Discovery (watcher sees the file) | `radio_capture.watcher.log` | `Discovered new recording: ...` |
| 3. Classification | `radio_capture.watcher.log` | `Classified recording N as 'speech'` |
| 4. ASR | `radio_capture.watcher.log` | `Transcribed recording N: ... chars` |
| 5. Summary + delivery | `radio_capture.run_summaries.log`, `radio_capture.daily_summary.log` | `Posted ... to <channel>` |

Fast checks, in order:

```bash
# 1. Did cron fire at all? (this marker is printed by the cron job itself)
docker logs radio-capture | grep '\[cron\]'

# 2. Pipeline health snapshot, logged by the watcher every 15 minutes
docker logs radio-capture-watcher | grep 'PIPELINE HEALTH'

# 3. What data does the summary actually see for a given day?
#    --dry-run touches neither OpenAI nor Telegram.
docker exec radio-capture python /app/daily_radio_summary.py \
  --date 2026-07-27 --timezone Asia/Jerusalem --target-language ru \
  --telegram-channel-id <id> --telegram-bot-token <token> --dry-run
```

Distinctive lines to grep for:

- `NOTHING TO SEND` / `NOTHING WAS SENT` — the run succeeded but had no material
- `PIPELINE HEALTH: ... ZERO transcripts` — ASR or the classifier is the problem
- `NO RECORDINGS at all` — capture side (ffmpeg / stream URL) is the problem
- `Telegram token check FAILED` / `is FORBIDDEN` — bot token revoked or bot removed from the channel
- `Telegram rejected` — message too long or Markdown the API cannot parse
- `Giving up on recording N` — that recording will never be transcribed

## Exit codes of the summary scripts

`daily_radio_summary.py` distinguishes:

| Code | Meaning |
|---|---|
| 0 | Summary generated and posted |
| 1 | Failure (OpenAI, Telegram, config) |
| 2 | Ran fine, but there was nothing to summarise |

`run_daily_summaries.py` logs code 2 as an explicit `NOTHING WAS SENT` warning
rather than a success, and relays every log line of the child process
(prefixed `[<channel>|out]`), so a failing channel always states its reason.

## Configuration

The radio capture application uses a centralized logging system with support for both console and file output.

### Enable Disk Logging

Set environment variable:
```bash
export ENABLE_RADIO_LOGS=true
```

Or add to `.env` file:
```env
ENABLE_RADIO_LOGS=true
```

### Log Directory

Default: `/data/logs/` (inside container)

Override with:
```env
LOG_DIR=/custom/path/logs
```

## Log Files

When disk logging is enabled, the following log files are created:

### System Logs
- **`radio_capture.api.log`** - Main FastAPI application logs
  - HTTP requests, authentication, database operations
  - API endpoint activity
  
- **`radio_capture.watcher.log`** - Recording watcher process logs
  - ASR processing status
  - Recording classification
  - Processing queue activity
  - `PIPELINE HEALTH` snapshot every 15 minutes (per-stream counts for the last
    24h: recordings / unclassified / speech / transcribed)
  - Also carries everything logged by `app.services.*` (watcher, ASR,
    classifier, stream manager). These modules use `logging.getLogger(__name__)`;
    `configure_app_logging()` in `logging_config.py` attaches the handlers, without
    which their records are dropped before reaching any file.

- **`radio_capture.daily_summary.log`** - Daily summary generation logs
  - OpenAI API calls
  - Summary generation process
  - Telegram notifications

- **`radio_capture.run_summaries.log`** - Batch summary runner logs
  - Orchestration of daily summaries
  - Channel configuration loading

### Stream-Specific Logs

Each active stream gets its own dedicated log file:
- **`stream_<id>_<name>.log`** - Individual stream ffmpeg process logs

Examples:
- `stream_1_Kan_Bet.log`
- `stream_2_Kan_Reka.log`
- `stream_3_TestPCM.log`

These files contain:
- FFmpeg process startup/shutdown
- Stream connection status
- Audio encoding information
- Segment creation events
- Error messages and warnings
- Buffer status and performance metrics

## Log Rotation

- **Frequency**: Every 3 days
- **Retention**: 10 backup files (30 days total)
- **Format**: Rotated files have date suffix (e.g., `radio_capture.api.log.2026-02-01`)
- **Automatic**: Old backups are automatically deleted when count exceeds 10

## Log Format

All logs use consistent formatting:
```
YYYY-MM-DD HH:MM:SS - logger_name - LEVEL - message
```

Example:
```
2026-02-01 14:30:15 - radio_capture.stream.1.Kan_Bet - INFO - Starting ffmpeg for stream: Kan Bet
2026-02-01 14:30:16 - radio_capture.stream.1.Kan_Bet - INFO - Stream opened successfully
2026-02-01 14:30:20 - radio_capture.stream.1.Kan_Bet - ERROR - Connection timeout
```

## Console Output

Console logging is **always enabled** regardless of disk logging settings. This ensures:
- Real-time monitoring via `docker logs`
- Debugging during development
- Visibility in container orchestration tools

## Benefits of Separate Stream Logs

1. **Troubleshooting**: Quickly identify issues with specific streams
2. **Performance Analysis**: Monitor individual stream behavior
3. **Resource Tracking**: Track memory and CPU usage per stream
4. **Audit Trail**: Complete history of each stream's activity
5. **Parallel Debugging**: Debug multiple streams simultaneously without log pollution

## Testing

Test the logging configuration:
```bash
# Without file logging
python tests/test_logging.py

# With file logging enabled
ENABLE_RADIO_LOGS=true LOG_DIR=./test_logs python tests/test_logging.py
```

## Viewing Logs

### In Docker
```bash
# View API logs
docker logs radio-capture

# View watcher logs
docker logs radio-capture-watcher

# View specific stream log (when disk logging enabled)
docker exec radio-capture cat /data/logs/stream_1_Kan_Bet.log

# Follow stream logs in real-time
docker exec radio-capture tail -f /data/logs/stream_1_Kan_Bet.log
```

### On Host (with volume mapping)
```bash
# Assuming DATA_DIR=./data
ls -lh data/logs/

# View API logs
cat data/logs/radio_capture.api.log

# Follow stream logs
tail -f data/logs/stream_1_Kan_Bet.log

# View all logs for a specific date
ls data/logs/*2026-02-01*
```

## Disk Space Considerations

With default settings (10 backups × 3-day rotation):
- Each log file stores approximately 3 days of activity
- Maximum 10 backups = 30 days of history per logger
- Estimate ~1-10 MB per day per stream (depends on activity level)
- Total for 3 streams: ~90-900 MB for 30 days

To reduce disk usage:
- Decrease backup count in `logging_config.py` (backupCount parameter)
- Increase rotation interval (interval parameter)
- Disable logging for specific streams if not needed
