import asyncio
import logging
import os
import subprocess
from datetime import datetime

from sqlmodel import Session, select

from app.core.db import engine
from app.models.models import Recording, Stream

logger = logging.getLogger(__name__)

class RecordingWatcher:
    def __init__(self):
        self.running = False

    async def start(self):
        self.running = True
        logger.info("Recording Watcher started.")
        asyncio.create_task(self.loop())

    async def loop(self):
        while self.running:
            try:
                await self.scan_files()
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
                        existing = session.exec(select(Recording).where(Recording.path == full_path)).first()
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
                            logger.info(f"Discovered new recording: {file}")
                        except Exception as e:
                            logger.error(f"Error processing file {file}: {e}")

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

watcher = RecordingWatcher()
