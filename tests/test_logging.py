#!/usr/bin/env python3
"""
Test script to verify logging configuration.
Run with: python test_logging.py
Or with file logging enabled: ENABLE_RADIO_LOGS=true python test_logging.py
"""
import os
import sys
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging_config import setup_logging, get_stream_logger

def test_logging():
    """Test logging with different levels and categories."""
    
    # Test main API logger
    api_logger = setup_logging("radio_capture.api")
    api_logger.info("API logger test message")
    
    # Test watcher logger
    watcher_logger = setup_logging("radio_capture.watcher")
    watcher_logger.info("Watcher logger test message")
    
    # Test stream-specific loggers
    stream1_logger = get_stream_logger("Kan Bet", 1)
    stream1_logger.info("Stream 1 (Kan Bet) - Starting ffmpeg process")
    stream1_logger.info("Stream 1 (Kan Bet) - Processing audio segments")
    stream1_logger.warning("Stream 1 (Kan Bet) - Buffer underrun detected")
    
    stream2_logger = get_stream_logger("Kan Reka", 2)
    stream2_logger.info("Stream 2 (Kan Reka) - Starting ffmpeg process")
    stream2_logger.info("Stream 2 (Kan Reka) - Connected to stream URL")
    
    # Show environment status
    enable_logs = os.getenv('ENABLE_RADIO_LOGS', 'not set')
    log_dir = os.getenv('LOG_DIR', '/data/logs (default)')
    
    print("\n" + "=" * 60)
    print("Environment Configuration:")
    print(f"  ENABLE_RADIO_LOGS: {enable_logs}")
    print(f"  LOG_DIR: {log_dir}")
    print("=" * 60)
    
    if enable_logs.lower() in ('true', '1', 'yes'):
        print(f"\nLog files created in: {log_dir}")
        print("  - radio_capture.api.log")
        print("  - radio_capture.watcher.log")
        print("  - stream_1_Kan_Bet.log")
        print("  - stream_2_Kan_Reka.log")
    else:
        print("\nSet ENABLE_RADIO_LOGS=true to enable file logging")
        print("All logs are currently only output to console")
    
    return True

if __name__ == "__main__":
    print("\n=== Testing Logging Configuration ===\n")
    test_logging()
    print("\n=== Test Complete ===\n")
