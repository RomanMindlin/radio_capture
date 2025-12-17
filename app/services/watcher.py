import asyncio
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.core.db import engine
from app.models.models import Recording, Stream
from app.services.audio_classifier import classify_audio
from app.services.asr import transcribe

logger = logging.getLogger(__name__)
DEFAULT_RETENTION_DAYS = 3

class RecordingWatcher:
    def __init__(self):
        self.running = False
        self._last_cleanup: datetime | None = None
        # Thread pool for CPU-intensive tasks (classification and ASR)
        # max_workers=1 ensures only one file is processed at a time
        # we need it since ASR isn't thread-safe
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="watcher-worker")

    async def start(self):
        self.running = True
        logger.info("Recording Watcher started.")
        asyncio.create_task(self.loop())

    async def loop(self):
        while self.running:
            try:
                await self.scan_files()
                await self.maybe_cleanup_old_recordings()
            except Exception as e:
                logger.error(f"Error in watcher loop: {e}")
            await asyncio.sleep(60) # Scan every minute

    async def scan_files(self):
        with Session(engine) as session:
            streams = session.exec(select(Stream)).all()
            for stream in streams:
                if not stream.enabled: continue
                
                # Check stream dir
                # Pattern: /data/recordings/{stream.name}/{YYYY}/{MM}/{DD}/
                # We need to walk recursively? Or just check recent folders?
                # For efficiency, we only check Today and Yesterday?
                # Or we just walk the whole tree (might be slow if millions of files).
                # Better: Since we name files by timestamp, we can just check if file is in DB.
                # Project requirement: "Creates a recordings entry whenever a segment is created".
                
                base_dir = f"/data/recordings/{stream.name}"
                if not os.path.exists(base_dir): continue
                
                for root, _, files in os.walk(base_dir):
                    for file in files:
                        if not file.endswith((".wav", ".mp3")): continue
                        
                        full_path = os.path.join(root, file)
                        
                        # Optimization: check if we already have this path
                        # Ideally we use a cache or bloom filter, but SQL is okay for <100k files.
                        # We can query by path.
                        existing = session.exec(
                            select(Recording).where(
                                Recording.path == full_path,
                                Recording.status != "deleted"
                            )
                        ).first()
                        if existing:
                            continue
                            
                        # It's new. Stats?
                        try:
                            stats = os.stat(full_path)
                            size = stats.st_size
                            
                            # Skip if file is being written (modified < 10s ago)
                            if datetime.now().timestamp() - stats.st_mtime < 10:
                                continue

                            duration = self.get_duration(full_path)
                            
                            # Parse start time
                            # chunk_20230101120000.mp3
                            ts_str = file.split("_")[1].split(".")[0]
                            start_ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                            
                            # Create recording entry immediately without classification/ASR
                            rec = Recording(
                                stream_id=stream.id,
                                path=full_path,
                                start_ts=start_ts,
                                size_bytes=size,
                                duration_seconds=duration,
                                status="completed"
                            )
                            session.add(rec)
                            session.commit()
                            session.refresh(rec)
                            logger.info(f"Discovered new recording: {file} (ID: {rec.id})")
                            
                            # Schedule classification and ASR in background thread
                            stream_language = stream.language if hasattr(stream, 'language') and stream.language else "he"
                            asyncio.create_task(
                                self._process_recording_async(rec.id, full_path, stream_language)
                            )
                        except Exception as e:
                            logger.error(f"Error processing file {file}: {e}")

    async def _process_recording_async(self, recording_id: int, file_path: str, language: str):
        """
        Process recording in background thread: classify and transcribe if needed.
        This runs asynchronously to avoid blocking the main watcher loop.
        """
        try:
            # Run classification in thread pool
            loop = asyncio.get_event_loop()
            classification = await loop.run_in_executor(
                self._executor,
                classify_audio,
                file_path
            )
            logger.info(f"Classified recording {recording_id} as '{classification}'")
            
            # Update database with classification
            with Session(engine) as session:
                recording = session.get(Recording, recording_id)
                if recording:
                    recording.classification = classification
                    session.add(recording)
                    session.commit()
            
            # If speech, run ASR in thread pool
            if classification == "speech":
                logger.info(f"Starting ASR for recording {recording_id} with language {language}")
                result = await loop.run_in_executor(
                    self._executor,
                    transcribe,
                    file_path,
                    "tiny",
                    language
                )
                
                # Update database with transcription
                with Session(engine) as session:
                    recording = session.get(Recording, recording_id)
                    if recording:
                        recording.transcript = result["transcript"]
                        recording.transcript_json = {"segments": result["segments"]}
                        recording.asr_model = result["model"]
                        recording.asr_confidence = result["confidence"]
                        recording.asr_ts = datetime.utcnow()
                        session.add(recording)
                        session.commit()
                        logger.info(f"Transcribed recording {recording_id}: {len(result['transcript'])} chars, {len(result['segments'])} segments")
            else:
                logger.info(f"Skipping ASR for recording {recording_id} (classification: {classification})")
                
        except Exception as e:
            logger.error(f"Error processing recording {recording_id}: {e}")

    def get_duration(self, path: str) -> float:
        try:
            cmd = [
                "ffprobe", 
                "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception as e:
            logger.error(f"Error getting duration for {path}: {e}")
        return 0.0

    async def maybe_cleanup_old_recordings(self):
        """
        Periodically purge recordings past their retention period (default 3 days) and mark them deleted in DB.
        """
        now = datetime.utcnow()
        # Run cleanup at most once per hour to limit disk churn
        if self._last_cleanup and (now - self._last_cleanup) < timedelta(hours=1):
            return

        await asyncio.to_thread(self.cleanup_old_recordings)
        self._last_cleanup = now

    def cleanup_old_recordings(self):
        utc_now = datetime.utcnow()
        with Session(engine) as session:
            streams = session.exec(select(Stream)).all()

            for stream in streams:
                retention_days = self._resolve_retention_days(stream)
                if retention_days == 0:
                    continue

                cutoff = utc_now - timedelta(days=retention_days)
                old_recordings = session.exec(
                    select(Recording)
                    .where(
                        Recording.stream_id == stream.id,
                        Recording.start_ts < cutoff,
                        Recording.status != "deleted"
                    )
                    .order_by(Recording.start_ts)
                    .limit(500)
                ).all()

                if not old_recordings:
                    continue

                logger.info(
                    f"Cleaning up {len(old_recordings)} recordings for stream {stream.name} "
                    f"older than {retention_days} day(s)."
                )

                for recording in old_recordings:
                    try:
                        if recording.path and os.path.exists(recording.path):
                            os.remove(recording.path)
                            logger.info(f"Deleted old recording file {recording.path}")
                        elif recording.path:
                            logger.warning(f"Recording file already missing: {recording.path}")

                        recording.status = "deleted"
                        session.add(recording)
                        session.commit()
                    except Exception as e:
                        session.rollback()
                        logger.error(f"Failed to delete recording {recording.id}: {e}")

    def _resolve_retention_days(self, stream: Stream) -> int:
        params = stream.optional_params or {}
        raw_value = params.get("retention_days", DEFAULT_RETENTION_DAYS)
        try:
            days = int(raw_value)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid retention_days '%s' for stream %s. Falling back to %s days.",
                raw_value,
                stream.name,
                DEFAULT_RETENTION_DAYS,
            )
            return DEFAULT_RETENTION_DAYS

        if days <= 0:
            logger.debug(
                "Retention disabled for stream %s because retention_days=%s",
                stream.name,
                raw_value,
            )
            return 0
        return days

watcher = RecordingWatcher()
