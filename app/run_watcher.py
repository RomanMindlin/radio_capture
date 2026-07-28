"""
Standalone recording watcher process.
This script runs independently of the API server to avoid affecting API performance.
"""
import asyncio
import logging
import os
import sys

from app.core.logging_config import (
    configure_app_logging,
    log_unhandled_exceptions,
    setup_logging,
)
from app.services.watcher import watcher

logger = setup_logging("radio_capture.watcher")
# Without this, everything logged by app.services.watcher / asr / audio_classifier
# is silently discarded (see configure_app_logging docstring).
configure_app_logging("radio_capture.watcher")
log_unhandled_exceptions(logger)


async def main():
    """Run the watcher process."""
    logger.info("Starting standalone recording watcher process...")
    logger.info(
        "Watcher environment: pid=%s WHISPER_DEVICE=%s PANNS_CACHE_DIR=%s "
        "WHISPER_CACHE_DIR=%s DATABASE_URL=%s",
        os.getpid(),
        os.getenv("WHISPER_DEVICE", "<auto>"),
        os.getenv("PANNS_CACHE_DIR", "/data/models/panns"),
        os.getenv("WHISPER_CACHE_DIR", "/data/models/whisper"),
        os.getenv("DATABASE_URL", "<default>"),
    )

    # Start the watcher
    await watcher.start()
    
    # Keep the process running
    try:
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour at a time
    except KeyboardInterrupt:
        logger.info("Shutting down watcher process...")
    except Exception as e:
        logger.error(f"Fatal error in watcher process: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
