#!/usr/bin/env python3
"""
Run Daily Summaries Script
Reads channels.json and executes daily_radio_summary.py for each configured channel.
"""

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

from app.core.logging_config import setup_logging

# Configure logging
logger = setup_logging("radio_capture.run_summaries")

# Keep in sync with daily_radio_summary.py
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING_TO_SEND = 2


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run daily radio summary for all configured channels"
    )
    parser.add_argument(
        "--config",
        default="channels.json",
        help="Path to channels configuration file (default: channels.json)"
    )
    parser.add_argument(
        "--date",
        help="Date in YYYY-MM-DD format (default: yesterday)"
    )
    return parser.parse_args()


def load_channels_config(config_path: str) -> Dict:
    """
    Load channels configuration from JSON file.
    
    Args:
        config_path: Path to the configuration file
    
    Returns:
        Configuration dictionary
    """
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        if "channels" not in config:
            logger.error(f"Invalid config file: 'channels' key not found")
            sys.exit(1)
        
        logger.info(f"Loaded configuration with {len(config['channels'])} channel(s)")
        return config
    
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        sys.exit(1)


def _relay_child_output(channel_id: str, stream_name: str, raw: bytes) -> None:
    """
    Re-log the child process output line by line.

    The child logs everything (including its errors) to stdout, so the previous
    code — which logged stdout at DEBUG and only printed stderr on failure —
    threw away every explanation of why a channel had failed.
    """
    if not raw:
        return
    for line in raw.decode(errors="replace").splitlines():
        if line.strip():
            logger.info("[%s|%s] %s", channel_id, stream_name, line.rstrip())


async def run_summary_for_channel(
    channel: Dict,
    date_str: str,
    script_path: Path
) -> bool:
    """
    Run daily_radio_summary.py for a single channel.

    Args:
        channel: Channel configuration dictionary
        date_str: Date string in YYYY-MM-DD format
        script_path: Path to daily_radio_summary.py script

    Returns:
        True if successful, False otherwise
    """
    channel_id = channel.get("telegram_channel_id")
    logger.info(f"Processing channel: {channel_id}")

    # Build command
    cmd = [
        sys.executable,
        str(script_path),
        "--date", date_str,
        "--timezone", channel["timezone"],
        "--target-language", channel["target_language"],
        "--telegram-channel-id", channel["telegram_channel_id"],
        "--telegram-bot-token", channel["telegram_bot_token"]
    ]

    started = time.monotonic()

    try:
        # Run the subprocess
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()
        elapsed = time.monotonic() - started

        # Always relay the child's own log lines, success or failure.
        _relay_child_output(channel_id, "out", stdout)
        _relay_child_output(channel_id, "err", stderr)

        if process.returncode == EXIT_OK:
            logger.info(
                f"✓ Channel {channel_id}: summary posted (took {elapsed:.1f}s)"
            )
            return True

        if process.returncode == EXIT_NOTHING_TO_SEND:
            # Used to look identical to success in the logs, which is exactly how
            # days of silence went unnoticed.
            logger.warning(
                f"✗ Channel {channel_id}: NOTHING WAS SENT — no transcribed speech "
                f"was available for {date_str} (took {elapsed:.1f}s). "
                f"Check the watcher / ASR pipeline."
            )
            return False

        logger.error(
            f"✗ Channel {channel_id}: failed with exit code {process.returncode} "
            f"(took {elapsed:.1f}s) — see the [{channel_id}|out] lines above for the cause"
        )
        return False

    except Exception as e:
        logger.error(
            f"✗ Exception while processing channel {channel_id}: {e}",
            exc_info=True,
        )
        return False


async def main():
    """Main execution function."""
    args = parse_args()
    
    # This banner is the proof that cron actually fired. If it is missing from the
    # logs for a day, the problem is the schedule/cron daemon, not the summary code.
    logger.info("=== Run Daily Summaries Script ===")
    logger.info(
        "Started at %s UTC | host=%s pid=%s cwd=%s",
        datetime.utcnow().isoformat(timespec="seconds"),
        socket.gethostname(),
        os.getpid(),
        os.getcwd(),
    )
    logger.info(
        "Environment: OPENAI_API_KEY=%s DATABASE_URL=%s ENABLE_RADIO_LOGS=%s LOG_DIR=%s",
        "set" if os.getenv("OPENAI_API_KEY") else "MISSING",
        os.getenv("DATABASE_URL", "<default>"),
        os.getenv("ENABLE_RADIO_LOGS", "<unset>"),
        os.getenv("LOG_DIR", "<default>"),
    )

    # Determine date to process
    if args.date:
        date_str = args.date
        try:
            # Validate date format
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            logger.error(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")
            sys.exit(1)
    else:
        # Default to yesterday, in the *container* local time (UTC in Docker), not
        # in each channel's timezone — worth knowing when a summary looks shifted.
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")

    logger.info(
        "Processing date: %s (local now=%s, UTC now=%s, source=%s)",
        date_str,
        datetime.now().isoformat(timespec="seconds"),
        datetime.utcnow().isoformat(timespec="seconds"),
        "--date argument" if args.date else "yesterday in container local time",
    )

    # Load configuration
    config = load_channels_config(args.config)
    channels = config["channels"]
    
    if not channels:
        logger.warning("No channels configured")
        sys.exit(0)
    
    # Locate daily_radio_summary.py script
    script_path = Path(__file__).parent / "daily_radio_summary.py"
    if not script_path.exists():
        logger.error(f"daily_radio_summary.py not found at: {script_path}")
        sys.exit(1)
    
    # Process each channel
    results = []
    for channel in channels:
        # Validate required fields
        required_fields = ["timezone", "target_language", "telegram_channel_id", "telegram_bot_token"]
        missing_fields = [field for field in required_fields if field not in channel]
        
        if missing_fields:
            logger.error(f"Channel missing required fields: {missing_fields}")
            results.append(False)
            continue
        
        success = await run_summary_for_channel(channel, date_str, script_path)
        results.append(success)
    
    # Summary
    total = len(results)
    successful = sum(results)
    failed = total - successful
    
    logger.info("=== Summary ===")
    logger.info(f"Total channels: {total}")
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")

    if failed > 0:
        logger.warning(
            f"{failed} of {total} channel(s) received NO summary for {date_str}"
        )
        sys.exit(1)
    else:
        logger.info("All channels processed successfully")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
