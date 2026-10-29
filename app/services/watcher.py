import asyncio
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import func, text
from sqlmodel import Session, select

from app.core.db import engine
from app.models.models import Recording, Stream
from app.services.audio_classifier import classify_audio
from app.services.asr import transcribe

logger = logging.getLogger(__name__)
DEFAULT_RETENTION_DAYS = 3
# How often to log the end-to-end pipeline health snapshot.
HEALTH_LOG_INTERVAL = timedelta(minutes=15)
# How far back to look for recordings that never got classified/transcribed.
STUCK_LOOKBACK_HOURS = 72
# Give up re-queueing a recording after this many failed attempts.
MAX_PROCESS_ATTEMPTS = 3
# Cap new discoveries per scan cycle. Each discovery runs ffprobe and a commit,
# so an unbounded backlog (e.g. after ASR was down for a while) would keep the
# scan busy for hours and delay the very classification/ASR work that drains
# the backlog. Capping lets each cycle return promptly; the remaining files are
# picked up on the next cycle, once already-queued recordings have had a
# chance to process.
MAX_DISCOVER_PER_CYCLE = 500
# Rows marked deleted are kept this long so the dashboard's 30-day activity
# chart (get_detailed_stats) still sees them, then removed for good. Without
# this the table only ever grows: deleted rows still carry their transcripts.
DELETED_ROW_RETENTION_DAYS = 31
PURGE_INTERVAL = timedelta(days=1)
# Delete in batches so each write transaction (and the lock it holds on the
# database shared with the API) stays short.
PURGE_BATCH_SIZE = 10000

class RecordingWatcher:
    def __init__(self):
        self.running = False
        self._last_cleanup: datetime | None = None
        self._last_purge: datetime | None = None
        self._last_health_log: datetime | None = None
        # Strong references to in-flight processing tasks. Without this the event
        # loop only keeps a weak reference and tasks can be garbage collected
        # mid-run, losing the recording silently.
        self._tasks: set[asyncio.Task] = set()
        # recording_id -> number of processing attempts (classification/ASR)
        self._attempts: dict[int, int] = {}
        # recording ids currently queued or being processed
        self._in_flight: set[int] = set()
        # Separate single-thread pools for classification and ASR. Each model is
        # only ever touched by its own thread (ASR isn't thread-safe), but the two
        # stages run concurrently, so a backlog of ASR jobs no longer blocks
        # classification of new recordings (and vice versa).
        self._classify_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="watcher-classify")
        self._asr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="watcher-asr")

    async def start(self):
        self.running = True
        logger.info("Recording Watcher started.")
        asyncio.create_task(self.loop())

    async def loop(self):
        while self.running:
            try:
                await self.scan_files()
                await self.requeue_stuck_recordings()
                await self.maybe_cleanup_old_recordings()
                await self.maybe_purge_deleted_rows()
                self.maybe_log_pipeline_health()
            except Exception as e:
                logger.error(f"Error in watcher loop: {e}", exc_info=True)
            await asyncio.sleep(60) # Scan every minute

    def _spawn(self, coro) -> None:
        """Schedule a background task and keep a strong reference to it."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _spawn_processing(self, recording_id: int, file_path: str, language: str) -> bool:
        """Queue classification/ASR for a recording unless it is already in flight."""
        if recording_id in self._in_flight:
            return False
        self._in_flight.add(recording_id)
        self._spawn(self._process_recording_async(recording_id, file_path, language))
        return True

    async def scan_files(self):
        # The walk, DB lookups and ffprobe calls run in a worker thread so they
        # don't block the event loop; tasks must be spawned back on the loop.
        discovered, capped = await asyncio.to_thread(self._discover_new_files)
        for recording_id, full_path, language in discovered:
            self._spawn_processing(recording_id, full_path, language)

        if discovered:
            logger.info(
                "Scan cycle finished: %d new recording(s) queued, %d task(s) in flight%s",
                len(discovered),
                len(self._tasks),
                " — discovery cap hit, remaining files next cycle" if capped else "",
            )

    def _discover_new_files(self) -> tuple[list[tuple[int, str, str]], bool]:
        """
        Walk the recordings directories and create a Recording row for every
        audio file not yet in the database.

        Returns ([(recording_id, path, language), ...], capped).
        """
        discovered: list[tuple[int, str, str]] = []
        capped = False
        with Session(engine) as session:
            streams = session.exec(select(Stream)).all()
            enabled_streams = [s for s in streams if s.enabled]
            if not enabled_streams:
                logger.warning(
                    "Scan cycle: no enabled streams (%d stream(s) configured). "
                    "Nothing will be recorded, classified or summarised.",
                    len(streams),
                )
                return discovered, capped

            # One query per cycle instead of one per file on disk: a per-file
            # ORM lookup cost ~0.7ms, i.e. ~45s of CPU per cycle at 60k files,
            # which also starved the ASR thread of the GIL.
            known_paths = set(
                session.exec(
                    select(Recording.path).where(Recording.status != "deleted")
                ).all()
            )

            for stream in enabled_streams:
                if capped: break

                # Pattern: /data/recordings/{stream.name}/{YYYY}/{MM}/{DD}/
                base_dir = f"/data/recordings/{stream.name}"
                if not os.path.exists(base_dir):
                    logger.warning(
                        "Stream %s is enabled but its recordings directory does not exist: %s",
                        stream.name,
                        base_dir,
                    )
                    continue

                for root, _, files in os.walk(base_dir):
                    for file in files:
                        if not file.endswith((".wav", ".mp3")): continue
                        
                        full_path = os.path.join(root, file)
                        if full_path in known_paths:
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
                            known_paths.add(full_path)
                            logger.info(
                                f"Discovered new recording: {file} (ID: {rec.id}, "
                                f"stream={stream.name}, size={size}B, duration={duration:.1f}s)"
                            )

                            # Classification and ASR are scheduled by the caller
                            stream_language = stream.language if hasattr(stream, 'language') and stream.language else "he"
                            discovered.append((rec.id, full_path, stream_language))
                        except Exception as e:
                            logger.error(f"Error processing file {file}: {e}", exc_info=True)

                        if len(discovered) >= MAX_DISCOVER_PER_CYCLE:
                            capped = True
                            break

                    if capped:
                        break

        return discovered, capped

    async def _process_recording_async(self, recording_id: int, file_path: str, language: str):
        """
        Process recording in background thread: classify and transcribe if needed.
        This runs asynchronously to avoid blocking the main watcher loop.
        """
        attempt = self._attempts.get(recording_id, 0) + 1
        self._attempts[recording_id] = attempt
        started = datetime.utcnow()
        try:
            # Run classification in thread pool
            loop = asyncio.get_event_loop()
            classification = await loop.run_in_executor(
                self._classify_executor,
                classify_audio,
                file_path
            )
            classify_seconds = (datetime.utcnow() - started).total_seconds()
            logger.info(
                f"Classified recording {recording_id} as '{classification}' "
                f"in {classify_seconds:.1f}s (attempt {attempt})"
            )

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
                    self._asr_executor,
                    transcribe,
                    file_path,
                    "small",
                    language
                )
                
                # Update database with transcription
                with Session(engine) as session:
                    recording = session.get(Recording, recording_id)
                    if recording:
                        processing_time = result.get("processing_time")
                        recording.transcript = result["transcript"]
                        transcript_payload = {"segments": result["segments"]}
                        if processing_time is not None:
                            transcript_payload["processing_time"] = processing_time

                        recording.transcript_json = transcript_payload
                        recording.asr_model = result["model"]
                        recording.asr_confidence = result["confidence"]
                        recording.asr_processing_seconds = processing_time
                        recording.asr_ts = datetime.utcnow()
                        session.add(recording)
                        session.commit()
                        logger.info(
                            f"Transcribed recording {recording_id}: "
                            f"{len(result['transcript'])} chars, {len(result['segments'])} segments, "
                            f"confidence={result['confidence']:.2f}, "
                            f"asr_time={processing_time if processing_time is None else round(processing_time, 1)}s"
                        )
                        if not result["transcript"].strip():
                            logger.warning(
                                "Recording %s classified as speech but produced an EMPTY transcript "
                                "(file=%s, language=%s) — it will contribute nothing to the daily summary",
                                recording_id,
                                file_path,
                                language,
                            )
                    else:
                        logger.error(
                            "Recording %s vanished from the database before its transcript "
                            "could be stored — transcription lost",
                            recording_id,
                        )
            else:
                logger.info(f"Skipping ASR for recording {recording_id} (classification: {classification})")

            self._attempts.pop(recording_id, None)

        except Exception as e:
            # This used to be a single un-detailed line on a logger with no handlers,
            # which is why a broken classifier/ASR could stall the whole summary
            # pipeline without leaving a trace anywhere.
            logger.error(
                "Error processing recording %s (attempt %d/%d, file=%s): %s",
                recording_id,
                attempt,
                MAX_PROCESS_ATTEMPTS,
                file_path,
                e,
                exc_info=True,
            )
            if attempt >= MAX_PROCESS_ATTEMPTS:
                logger.error(
                    "Giving up on recording %s after %d attempts — it will never be "
                    "classified/transcribed and is lost for daily summaries",
                    recording_id,
                    attempt,
                )
        finally:
            self._in_flight.discard(recording_id)

    async def requeue_stuck_recordings(self):
        """
        Re-queue recordings that were discovered but never finished the pipeline:
        no classification at all, or classified as speech with no transcript.

        Without this a transient failure (model download, GPU hiccup, container
        restart mid-ASR) permanently removes a recording from the daily summary,
        because a recording is only ever processed at discovery time.
        """
        cutoff = datetime.utcnow() - timedelta(hours=STUCK_LOOKBACK_HOURS)

        with Session(engine) as session:
            stuck = session.exec(
                select(Recording, Stream)
                .join(Stream, Stream.id == Recording.stream_id)
                .where(
                    Recording.start_ts >= cutoff,
                    Recording.status != "deleted",
                    (Recording.classification.is_(None))
                    | ((Recording.classification == "speech") & (Recording.transcript.is_(None))),
                )
                .order_by(Recording.start_ts)
                .limit(200)
            ).all()

            if not stuck:
                return

            requeued = 0
            skipped_exhausted = 0
            skipped_missing = 0

            for recording, stream in stuck:
                if recording.id in self._in_flight:
                    continue
                if self._attempts.get(recording.id, 0) >= MAX_PROCESS_ATTEMPTS:
                    skipped_exhausted += 1
                    continue
                if not recording.path or not os.path.exists(recording.path):
                    skipped_missing += 1
                    continue

                language = stream.language or "he"
                if self._spawn_processing(recording.id, recording.path, language):
                    requeued += 1

            logger.warning(
                "Unfinished pipeline items in the last %dh: %d (requeued=%d, "
                "attempts exhausted=%d, audio file gone=%d, in flight=%d)",
                STUCK_LOOKBACK_HOURS,
                len(stuck),
                requeued,
                skipped_exhausted,
                skipped_missing,
                len(self._in_flight),
            )

    def maybe_log_pipeline_health(self):
        """Periodically log an end-to-end snapshot of the transcription pipeline."""
        now = datetime.utcnow()
        if self._last_health_log and (now - self._last_health_log) < HEALTH_LOG_INTERVAL:
            return

        try:
            self.log_pipeline_health()
        except Exception as e:
            logger.error(f"Failed to build pipeline health snapshot: {e}", exc_info=True)
        finally:
            self._last_health_log = now

    def log_pipeline_health(self):
        """
        Log per-stream counts for the last 24h: how many recordings exist, how many
        were classified, how many are speech, and how many actually carry a
        transcript. The daily summary only uses speech recordings with a transcript,
        so if `transcribed` is 0 here, the bot has nothing to post.
        """
        since = datetime.utcnow() - timedelta(hours=24)

        with Session(engine) as session:
            streams = session.exec(select(Stream).where(Stream.enabled == True)).all()

            if not streams:
                logger.warning(
                    "PIPELINE HEALTH (24h): no enabled streams — the daily summary "
                    "will have nothing to report"
                )
                return

            def _count(*conditions) -> int:
                statement = select(func.count()).select_from(Recording).where(
                    Recording.start_ts >= since, *conditions
                )
                return session.exec(statement).one()

            total_transcribed = 0

            for stream in streams:
                base = (Recording.stream_id == stream.id,)
                total = _count(*base)
                unclassified = _count(*base, Recording.classification.is_(None))
                speech = _count(*base, Recording.classification == "speech")
                transcribed = _count(
                    *base,
                    Recording.classification == "speech",
                    Recording.transcript.is_not(None),
                )
                total_transcribed += transcribed

                logger.info(
                    "PIPELINE HEALTH (24h) stream=%s status=%s: recordings=%d, "
                    "unclassified=%d, speech=%d, transcribed=%d, awaiting_asr=%d",
                    stream.name,
                    stream.current_status,
                    total,
                    unclassified,
                    speech,
                    transcribed,
                    speech - transcribed,
                )

                if total == 0:
                    logger.warning(
                        "PIPELINE HEALTH: stream %s produced NO recordings in the last 24h "
                        "(status=%s, last_up=%s, last_error=%s)",
                        stream.name,
                        stream.current_status,
                        stream.last_up,
                        stream.last_error,
                    )
                elif transcribed == 0:
                    logger.warning(
                        "PIPELINE HEALTH: stream %s has %d recording(s) but ZERO transcripts "
                        "in the last 24h — the daily summary will skip this stream",
                        stream.name,
                        total,
                    )

            if total_transcribed == 0:
                logger.error(
                    "PIPELINE HEALTH: no transcribed speech at all in the last 24h across "
                    "%d enabled stream(s) — the daily Telegram summary will send NOTHING",
                    len(streams),
                )

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

    async def maybe_purge_deleted_rows(self):
        """Once a day, permanently remove long-deleted recording rows."""
        now = datetime.utcnow()
        if self._last_purge and (now - self._last_purge) < PURGE_INTERVAL:
            return

        await asyncio.to_thread(self.purge_deleted_rows)
        self._last_purge = now

    def purge_deleted_rows(self):
        """
        Hard-delete rows marked deleted whose recording started more than
        DELETED_ROW_RETENTION_DAYS ago (their audio files are long gone).
        """
        cutoff = datetime.utcnow() - timedelta(days=DELETED_ROW_RETENTION_DAYS)
        statement = text("""
            DELETE FROM recording WHERE id IN (
                SELECT id FROM recording
                WHERE start_ts < :cutoff AND status = 'deleted'
                LIMIT :batch
            )
        """)

        purged = 0
        try:
            while True:
                with engine.begin() as conn:
                    deleted = conn.execute(
                        statement, {"cutoff": cutoff, "batch": PURGE_BATCH_SIZE}
                    ).rowcount
                purged += deleted
                if deleted < PURGE_BATCH_SIZE:
                    break
        except Exception as e:
            logger.error(f"Failed to purge deleted recording rows: {e}", exc_info=True)

        if purged:
            logger.info(
                "Purged %d recording row(s) marked deleted and older than %d days",
                purged,
                DELETED_ROW_RETENTION_DAYS,
            )

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
