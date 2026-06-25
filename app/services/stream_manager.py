import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlmodel import Session, select

from app.core.db import engine, get_session
from app.core.logging_config import get_stream_logger
from app.models.models import Event, Recording, Stream
from app.services.ffmpeg_builder import FfmpegBuilder

logger = logging.getLogger(__name__)

# If no audio file has been modified for this long, treat the stream as stalled.
_STALL_THRESHOLD_SECONDS = 300   # 5 minutes
# Don't check for stalls during the first N seconds after a stream starts.
_STALL_GRACE_SECONDS = 120


class StreamManager:
    def __init__(self):
        self.processes: Dict[int, asyncio.subprocess.Process] = {}
        self.retry_counts: Dict[int, int] = {}
        self.stream_loggers: Dict[int, logging.Logger] = {}
        self.stream_start_times: Dict[int, float] = {}  # monotonic time of last start
        self.running = False
        self._lock = asyncio.Lock()

    async def start(self):
        """Starts the manager loop."""
        self.running = True
        logger.info("Stream Manager started.")
        asyncio.create_task(self.monitor_loop())

    async def stop(self):
        """Stops all streams and the manager."""
        self.running = False
        logger.info("Stopping Stream Manager...")
        async with self._lock:
            for stream_id, proc in self.processes.items():
                if proc.returncode is None:
                    try:
                        proc.terminate()
                        await proc.wait()
                    except Exception as e:
                        logger.error(f"Error killing stream {stream_id}: {e}")
            self.processes.clear()

    async def monitor_loop(self):
        """Main loop to check stream status and restart if needed."""
        while self.running:
            try:
                await self.reconcile_streams()
                # Also ensure directories exist for tomorrow/today to prevent ffmpeg failure
                # This is a basic mitigation for the directory creation issue
                self.ensure_directories()
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
            
            await asyncio.sleep(10) # Check every 10 seconds

    def ensure_directories(self):
        """Pre-creates directories for active streams to satisfy ffmpeg output."""
        # We need to run this periodically. 
        # We'll creating dirs for Today and Tomorrow.
        with Session(engine) as session:
            streams = session.exec(select(Stream).where(Stream.enabled == True)).all()
            for stream in streams:
                try:
                    base = f"/data/recordings/{stream.name}"
                    for days_delta in [0, 1]:
                        # 0=today, 1=tomorrow
                        # Note: we need to handle timezone if configurable, defaulting to system/UTC
                        # Implementation detail: Using UTC for now as per container default
                        d = datetime.utcnow() # + delta...
                        # Actually simple approach: just make sure deep path exists.
                        # Using system time logic same as ffmpeg.
                        # If ffmpeg uses local time, we must match. 
                        # We'll assume UTC for container.
                        # Logic for adding delta is tricky without full datetime obj math, but straightforward.
                        target_date = d  # For today
                        if days_delta == 1:
                            target_date = d + timedelta(days=1)
                        
                        dir_path = target_date.strftime(f"{base}/%Y/%m/%d")
                        os.makedirs(dir_path, exist_ok=True)
                except Exception as e:
                    logger.error(f"Failed to create dirs for {stream.name}: {e}")

    async def reconcile_streams(self):
        """Syncs running processes with DB state."""
        async with self._lock:
            with Session(engine) as session:
                streams = session.exec(select(Stream)).all()
                active_stream_ids = set()

                for stream in streams:
                    if stream.enabled:
                        active_stream_ids.add(stream.id)
                        if stream.id not in self.processes:
                            await self.start_stream(stream, session)
                        else:
                            proc = self.processes[stream.id]
                            if proc.returncode is not None:
                                await self.handle_failure(stream, session)
                            else:
                                # Process is alive — check for silent stall.
                                await self._check_stall(stream, session)
                    else:
                        if stream.id in self.processes:
                            await self.stop_stream(stream.id)

    async def _check_stall(self, stream: Stream, session: Session):
        """Detect a silently-stalled ffmpeg process (alive but writing nothing)."""
        start_t = self.stream_start_times.get(stream.id)
        if start_t is None or (time.monotonic() - start_t) < _STALL_GRACE_SECONDS:
            return

        last_mtime = self._latest_audio_mtime(stream.name)
        stream_logger = self.stream_loggers.get(stream.id, logger)

        if last_mtime is None:
            # No file ever written since start — wait a full segment period before complaining.
            seg_time = stream.mandatory_params.get("segment_time", 3600)
            if (time.monotonic() - start_t) > seg_time + _STALL_GRACE_SECONDS:
                stream_logger.warning(
                    f"Stream {stream.name}: no audio file has been written "
                    f"{time.monotonic() - start_t:.0f}s after start. "
                    "Process may be stalled — restarting."
                )
                await self._restart_stalled(stream, session)
            return

        age = time.time() - last_mtime
        if age > _STALL_THRESHOLD_SECONDS:
            stream_logger.warning(
                f"Stream {stream.name}: last audio file not updated for {age:.0f}s "
                f"(threshold {_STALL_THRESHOLD_SECONDS}s). "
                "ffmpeg appears stalled — restarting."
            )
            await self._restart_stalled(stream, session)
        else:
            stream_logger.debug(
                f"Stream {stream.name}: last audio file updated {age:.0f}s ago — OK"
            )

    def _latest_audio_mtime(self, stream_name: str) -> Optional[float]:
        """Return the mtime of the most-recently modified audio file for this stream,
        checking only today's and yesterday's directories for efficiency."""
        base = f"/data/recordings/{stream_name}"
        if not os.path.exists(base):
            return None

        now = datetime.utcnow()
        dirs_to_check = [
            now.strftime(f"{base}/%Y/%m/%d"),
            (now - timedelta(days=1)).strftime(f"{base}/%Y/%m/%d"),
        ]

        latest: Optional[float] = None
        for d in dirs_to_check:
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                if not fname.endswith((".wav", ".mp3", ".aac")):
                    continue
                try:
                    mtime = os.path.getmtime(os.path.join(d, fname))
                    if latest is None or mtime > latest:
                        latest = mtime
                except OSError:
                    pass
        return latest

    async def _restart_stalled(self, stream: Stream, session: Session):
        """Force-stop a stalled stream so the next reconcile loop restarts it."""
        proc = self.processes.get(stream.id)
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:
                pass
        del self.processes[stream.id]
        self.stream_start_times.pop(stream.id, None)
        if stream.id in self.stream_loggers:
            del self.stream_loggers[stream.id]

        stream.current_status = "error"
        stream.last_error = "Stall detected: no audio output, process restarted"
        session.add(stream)
        event = Event(stream_id=stream.id, level="error",
                      message="Stream stall detected — restarted")
        session.add(event)
        session.commit()

    async def start_stream(self, stream: Stream, session: Session):
        logger.info(f"Starting stream: {stream.name}")
        
        # Create stream-specific logger
        stream_logger = get_stream_logger(stream.name, stream.id)
        self.stream_loggers[stream.id] = stream_logger
        
        try:
            # Ensure output dir exists (at least the root)
            os.makedirs(f"/data/recordings/{stream.name}", exist_ok=True)
            self.ensure_directories() # Ensure date dirs

            builder = FfmpegBuilder(stream.dict())
            cmd = builder.build_command()
            
            stream_logger.info(f"Starting ffmpeg for stream: {stream.name}")
            stream_logger.info(f"Command: {' '.join(cmd)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            self.processes[stream.id] = proc
            self.stream_start_times[stream.id] = time.monotonic()
            stream.current_status = "running"
            stream.last_up = datetime.utcnow()
            stream.last_error = None
            session.add(stream)
            session.commit()
            
            # Log event
            event = Event(stream_id=stream.id, level="info", message="Stream started")
            session.add(event)
            session.commit()
            
            # Spawn a log reader
            asyncio.create_task(self.monitor_output(stream.id, proc))

        except Exception as e:
            stream_logger.error(f"Failed to start stream {stream.name}: {e}")
            stream.current_status = "error"
            stream.last_error = str(e)
            session.add(stream)
            session.commit()

    async def stop_stream(self, stream_id: int):
        if stream_id in self.processes:
            proc = self.processes[stream_id]
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
            del self.processes[stream_id]
            self.stream_start_times.pop(stream_id, None)

            if stream_id in self.stream_loggers:
                stream_logger = self.stream_loggers[stream_id]
                stream_logger.info(f"Stream {stream_id} stopped")
                del self.stream_loggers[stream_id]
            
            # Update DB
            with Session(engine) as session:
                stream = session.get(Stream, stream_id)
                if stream:
                    stream.current_status = "stopped"
                    session.add(stream)
                    session.commit()

    async def handle_failure(self, stream: Stream, session: Session):
        proc = self.processes[stream.id]
        exit_code = proc.returncode
        stream_logger = self.stream_loggers.get(stream.id, logger)
        stream_logger.error(
            f"Stream {stream.name} (ID: {stream.id}) process exited unexpectedly "
            f"(exit code: {exit_code})"
        )

        del self.processes[stream.id]
        self.stream_start_times.pop(stream.id, None)

        if stream.id in self.stream_loggers:
            del self.stream_loggers[stream.id]

        stream.current_status = "error"
        stream.last_error = f"Process exited unexpectedly (exit code: {exit_code})"
        session.add(stream)
        session.commit()

        event = Event(
            stream_id=stream.id,
            level="error",
            message=f"Stream process exited (exit code: {exit_code})"
        )
        session.add(event)
        session.commit()

    async def monitor_output(self, stream_id: int, proc: asyncio.subprocess.Process):
        """Reads ffmpeg stderr to surface log lines and detect unexpected exits."""
        stream_logger = self.stream_loggers.get(stream_id, logger)

        try:
            if proc.stderr is None:
                stream_logger.warning(f"Stream {stream_id}: no stderr pipe available")
                return

            buffer = b""
            chunk_size = 4096

            while True:
                chunk = await proc.stderr.read(chunk_size)
                if not chunk:
                    break

                buffer += chunk
                lines = buffer.splitlines(keepends=True)
                if lines and not lines[-1].endswith((b"\n", b"\r")):
                    buffer = lines.pop()
                else:
                    buffer = b""

                for raw_line in lines:
                    self._log_stream_line(stream_logger, raw_line)

            if buffer:
                self._log_stream_line(stream_logger, buffer)

        except Exception as e:
            stream_logger.error(f"Stream {stream_id}: error reading ffmpeg output: {e}")
        finally:
            # Ensure the process is fully reaped so returncode is set.
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:
                pass
            exit_code = proc.returncode
            stream_logger.info(
                f"Stream {stream_id}: ffmpeg stderr closed (exit code: {exit_code})"
            )

    def _log_stream_line(self, stream_logger: logging.Logger, raw_line: bytes):
        line_str = raw_line.decode('utf-8', errors='ignore').strip()
        if not line_str:
            return

        line_lower = line_str.lower()
        if 'error' in line_lower or 'fatal' in line_lower:
            stream_logger.error(line_str)
        elif 'warning' in line_lower:
            stream_logger.warning(line_str)
        elif line_str.startswith("size=") or line_str.startswith("frame="):
            # ffmpeg progress stats — log at DEBUG to avoid flooding log files.
            stream_logger.debug(line_str)
        else:
            stream_logger.info(line_str)

manager = StreamManager()
