#!/usr/bin/env python3
"""
Daily Radio Summary Script
Aggregates speech transcriptions and publishes summaries to Telegram via OpenAI.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from aiogram import Bot
from sqlmodel import Session, select

# Import existing database setup
from app.core.db import engine
from app.models.models import Recording, Stream

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SUMMARY_INTRO_BY_LANGUAGE = {
    "en": "What people talked about on the radio today.",
    "he": "על מה אנשים דיברו ברדיו היום.",
    "de": "Worüber die Leute heute im Radio gesprochen haben.",
    "it": "Di cosa hanno parlato le persone alla radio oggi.",
    "sp": "De qué habló la gente en la radio hoy.",
    "fr": "De quoi les gens ont parlé à la radio aujourd'hui.",
    "ru": "О чем люди говорили по радио сегодня.",
}


def get_summary_intro(target_language: str) -> str:
    """Return localized intro line for the final Telegram message."""
    intro = SUMMARY_INTRO_BY_LANGUAGE.get(target_language)
    if intro:
        return intro
    logger.warning(
        "Unsupported target language '%s' for intro; falling back to English",
        target_language,
    )
    return SUMMARY_INTRO_BY_LANGUAGE["en"]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate and post daily radio summary to Telegram"
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Date in YYYY-MM-DD format"
    )
    parser.add_argument(
        "--timezone",
        required=True,
        help="IANA timezone name (e.g., Asia/Jerusalem)"
    )
    parser.add_argument(
        "--target-language",
        required=True,
        help="ISO language code for summary output"
    )
    parser.add_argument(
        "--telegram-channel-id",
        required=True,
        help="Telegram channel ID"
    )
    parser.add_argument(
        "--telegram-bot-token",
        required=True,
        help="Telegram bot token"
    )
    return parser.parse_args()


def compute_utc_range(date_str: str, timezone_str: str) -> tuple[datetime, datetime]:
    """
    Convert local date + timezone to UTC range [00:00:00, 23:59:59].
    
    Args:
        date_str: Date in YYYY-MM-DD format
        timezone_str: IANA timezone name
    
    Returns:
        Tuple of (start_utc, end_utc)
    """
    try:
        local_tz = ZoneInfo(timezone_str)
    except Exception as e:
        logger.error(f"Invalid timezone '{timezone_str}': {e}")
        sys.exit(1)
    
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        logger.error(f"Invalid date format '{date_str}': {e}")
        sys.exit(1)
    
    # Create datetime objects at day boundaries in local timezone
    start_local = datetime.combine(date_obj, time(0, 0, 0), tzinfo=local_tz)
    end_local = datetime.combine(date_obj, time(23, 59, 59), tzinfo=local_tz)
    
    # Convert to UTC
    start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_utc = end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    
    logger.info(f"Date range: {start_local} to {end_local} ({timezone_str})")
    logger.info(f"UTC range: {start_utc} to {end_utc}")
    
    return start_utc, end_utc


def fetch_enabled_streams(session: Session) -> List[Stream]:
    """
    Fetch all enabled streams.
    
    Args:
        session: Database session
    
    Returns:
        List of enabled Stream objects
    """
    statement = select(Stream).where(Stream.enabled == True).order_by(Stream.name)
    streams = session.exec(statement).all()
    logger.info(f"Found {len(streams)} enabled streams")
    return streams


def fetch_recordings_for_stream(
    session: Session, 
    stream_id: int, 
    start_utc: datetime, 
    end_utc: datetime
) -> List[Recording]:
    """
    Fetch speech recordings with transcriptions for a specific stream.
    
    Args:
        session: Database session
        stream_id: Stream ID
        start_utc: Start of time range (UTC)
        end_utc: End of time range (UTC)
    
    Returns:
        List of Recording objects
    """
    statement = (
        select(Recording)
        .where(Recording.stream_id == stream_id)
        .where(Recording.classification == "speech")
        .where(Recording.transcript.is_not(None))
        .where(Recording.start_ts >= start_utc)
        .where(Recording.start_ts <= end_utc)
        .order_by(Recording.start_ts)
    )
    
    results = session.exec(statement).all()
    
    return results


def build_llm_prompt_for_stream(
    stream_name: str,
    stream_language: str,
    transcriptions: List[Dict],
    target_language: str
) -> str:
    """
    Build the prompt for OpenAI LLM for a single stream.
    
    Args:
        stream_name: Name of the radio station
        stream_language: Language code of the stream
        transcriptions: List of transcript_json objects
        target_language: ISO language code for output
    
    Returns:
        Complete prompt string
    """
    prompt_parts = [
        f"You are a radio content analyst. Analyze the following radio transcriptions and produce a summary.",
        "",
        f"Station: {stream_name}",
        f"Original language: {stream_language}",
        f"Output language: {target_language}",
        "",
        "Task:",
        "- Identify 3-5 main topics discussed during the day",
        "- For each topic, capture key points and insights",
        "- Write ONE coherent item summarizing one topic",
        "- Write each topic as a separate paragraph",
        "- Use clear and concise language",
        f"- Write the summary ONLY in {target_language}",
        "",
        "Topics may include:",
        "- News and current events",
        "- Politics",
        "- Economy",
        "- Culture",
        "- Public discussions",
        "- Interviews and studio guests",
        "",
        "Do NOT mention:",
        "- Technical details",
        "- Timecodes",
        "- Speaker labels",
        "- Recognition process",
        "",
        "Output format:",
        "Return ONLY a single paragraph summary. No heading, no station name, just the summary paragraph.",
        "",
        "===== TRANSCRIPTION DATA FORMAT =====",
        "",
        "Each segment represents a continuous fragment of spoken audio.",
        "Important notes for interpretation:",
        "- Segments should be read sequentially to reconstruct the meaning of the broadcast.",
        "- Do not rely on timestamps or speaker fields for output.",
        "- Focus on understanding the semantic content and topics discussed across all segments.",
        "- The transcription reflects real radio speech and may include informal language, overlaps, or unfinished thoughts.",
        "- Do NOT invent facts. If uncertain, keep it generic and lower confidence."
        "- If two adjacent parts are the same segment type and topic, keep them as ONE segment (do not over-split)"
        "",
        "===== TRANSCRIPTION DATA =====",
        ""
    ]
    
    prompt_parts.append(json.dumps(transcriptions, ensure_ascii=False))
    
    return "\n".join(prompt_parts)


def call_openai(prompt: str) -> str:
    """
    Call OpenAI Chat Completions API.
    
    Args:
        prompt: Complete prompt text
    
    Returns:
        LLM response text
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY environment variable not set")
        sys.exit(1)
    
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gpt-5-mini",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    
    logger.info("Calling OpenAI API...")
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"OpenAI API request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        sys.exit(1)
    
    try:
        result = response.json()
        summary_text = result["choices"][0]["message"]["content"]
        logger.info("OpenAI API call successful")
        return summary_text
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse OpenAI response: {e}")
        logger.error(f"Response: {response.text}")
        sys.exit(1)


async def post_to_telegram(text: str, channel_id: str, bot_token: str) -> None:
    """
    Post message to Telegram channel using aiogram.
    
    Args:
        text: Message text
        channel_id: Telegram channel ID
        bot_token: Telegram bot token
    """
    logger.info(f"Posting to Telegram channel {channel_id}...")
    
    bot = Bot(token=bot_token)
    
    try:
        await bot.send_message(chat_id=channel_id, text=text, parse_mode="Markdown")
        logger.info("Successfully posted to Telegram")
    except Exception as e:
        logger.error(f"Telegram API request failed: {e}")
        sys.exit(1)
    finally:
        await bot.session.close()


async def main():
    """Main execution function."""
    args = parse_args()
    
    logger.info("=== Daily Radio Summary Script ===")
    logger.info(f"Date: {args.date}")
    logger.info(f"Timezone: {args.timezone}")
    logger.info(f"Target language: {args.target_language}")
    logger.info(f"Telegram channel: {args.telegram_channel_id}")
    
    # Compute UTC time range
    start_utc, end_utc = compute_utc_range(args.date, args.timezone)
    
    # Collect summaries for all streams
    stream_summaries = []
    
    with Session(engine) as session:
        # Get all enabled streams
        streams = fetch_enabled_streams(session)
        
        if not streams:
            logger.warning("No enabled streams found")
            logger.info("Exiting without sending Telegram message")
            sys.exit(0)
        
        # Process each stream
        for stream in streams:
            logger.info(f"Processing stream: {stream.name}")
            
            # Fetch recordings for this stream
            recordings = fetch_recordings_for_stream(
                session, stream.id, start_utc, end_utc
            )
            
            if not recordings:
                logger.info(f"  No recordings found for {stream.name}, skipping")
                continue
            
            logger.info(f"  Found {len(recordings)} recordings")
            
            # Extract transcript from recordings
            transcriptions = [r.transcript for r in recordings]
            
            # Build prompt for this stream
            prompt = build_llm_prompt_for_stream(
                stream.name,
                stream.language,
                transcriptions,
                args.target_language
            )
            
            # Call OpenAI for this stream
            summary = call_openai(prompt)
            
            # Store the summary
            stream_summaries.append({
                "name": stream.name,
                "summary": summary.strip()
            })
            
            logger.info(f"  Summary generated for {stream.name}")
    
    # Check if we have any summaries
    if not stream_summaries:
        logger.warning("No recordings found for any enabled stream")
        logger.info("Exiting without sending Telegram message")
        sys.exit(0)
    
    # Build final message
    message_parts = [get_summary_intro(args.target_language), ""]
    
    for item in stream_summaries:
        message_parts.append(f"*{item['name']}* — {item['summary']}")
        message_parts.append("")
    
    final_message = "\n".join(message_parts).strip()
    
    # Post to Telegram
    await post_to_telegram(final_message, args.telegram_channel_id, args.telegram_bot_token)
    
    logger.info("=== Script completed successfully ===")


if __name__ == "__main__":
    asyncio.run(main())
