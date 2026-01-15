"""
Standalone recording watcher process.
This script runs independently of the API server to avoid affecting API performance.
"""
import asyncio
import logging
import sys

from app.services.watcher import watcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Run the watcher process."""
    logger.info("Starting standalone recording watcher process...")
    
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
