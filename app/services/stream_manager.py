import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, Optional

from sqlmodel import Session, select

from app.core.db import engine, get_session
from app.models.models import Event, Recording, Stream
from app.services.ffmpeg_builder import FfmpegBuilder

logger = logging.getLogger(__name__)

class StreamManager:
    def __init__(self):
        self.processes: Dict[int, asyncio.subprocess.Process] = {}
        self.retry_counts: Dict[int, int] = {}
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
                            # Add one day
                            # Quick hack for pure datetime addition without timedelta import conflict if any
                            # We should import timedelta
                            from datetime import timedelta
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
                            # Start it
                            await self.start_stream(stream, session)
                        else:
                            # Check if dead
                            proc = self.processes[stream.id]
                            if proc.returncode is not None:
                                # It died
                                await self.handle_failure(stream, session)
                    else:
                        # Should be stopped
                        if stream.id in self.processes:
                            await self.stop_stream(stream.id)

    async def start_stream(self, stream: Stream, session: Session):
        logger.info(f"Starting stream: {stream.name}")
        try:
            # Ensure output dir exists (at least the root)
            os.makedirs(f"/data/recordings/{stream.name}", exist_ok=True)
            self.ensure_directories() # Ensure date dirs

            builder = FfmpegBuilder(stream.dict())
            cmd = builder.build_command()
            
            logger.info(f"Command for {stream.name}: {' '.join(cmd)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            self.processes[stream.id] = proc
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
            logger.error(f"Failed to start {stream.name}: {e}")
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
            
            # Update DB
            with Session(engine) as session:
                stream = session.get(Stream, stream_id)
                if stream:
                    stream.current_status = "stopped"
                    session.add(stream)
                    session.commit()

    async def handle_failure(self, stream: Stream, session: Session):
        # Clean up process handle
        del self.processes[stream.id]
        
        # Update Status
        stream.current_status = "error"
        stream.last_error = "Process exited unexpectedly"
        session.add(stream)
        session.commit()
        
        event = Event(stream_id=stream.id, level="error", message="Stream process died")
        session.add(event)
        session.commit()
        
        # Retry logic could be here (count retries, delay)
        # For now, let the next loop iteration pick it up immediately
        # But we should probably add a delay or check 'retry policy'
        # Simple policy: just restart on next loop (10s delay implicitly)

    async def monitor_output(self, stream_id: int, proc: asyncio.subprocess.Process):
        """Reads stderr/stdout to log events or detect issues."""
        # ffmpeg logs to stderr mainly
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='ignore').strip()
                if line_str:
                    # We can parse specific ffmpeg messages here
                    # For example "Opening '...'" or error codes
                    # For now just debug log
                    logger.info(f"[{stream_id}] {line_str}")
        except Exception:
            pass

manager = StreamManager()
