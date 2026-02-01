#!/usr/bin/env python3
"""
Run Daily Summaries Script
Reads channels.json and executes daily_radio_summary.py for each configured channel.
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

from app.core.logging_config import setup_logging

# Configure logging
logger = setup_logging("radio_capture.run_summaries")


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
    logger.info(f"Processing channel: {channel.get('telegram_channel_id')}")
    
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
    
    try:
        # Run the subprocess
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"✓ Successfully processed channel {channel.get('telegram_channel_id')}")
            if stdout:
                logger.debug(f"Output: {stdout.decode()}")
            return True
        else:
            logger.error(f"✗ Failed to process channel {channel.get('telegram_channel_id')}")
            if stderr:
                logger.error(f"Error: {stderr.decode()}")
            return False
    
    except Exception as e:
        logger.error(f"✗ Exception while processing channel {channel.get('telegram_channel_id')}: {e}")
        return False


async def main():
    """Main execution function."""
    args = parse_args()
    
    logger.info("=== Run Daily Summaries Script ===")
    
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
        # Default to yesterday
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")
    
    logger.info(f"Processing date: {date_str}")
    
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
        logger.warning(f"{failed} channel(s) failed to process")
        sys.exit(1)
    else:
        logger.info("All channels processed successfully")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
