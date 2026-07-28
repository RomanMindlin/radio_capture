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
import socket
import sys
import time
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from sqlalchemy import func
from sqlmodel import Session, select

# Import existing database setup
from app.core.db import engine
from app.core.logging_config import setup_logging
from app.models.models import Recording, Stream

# Configure logging
logger = setup_logging("radio_capture.daily_summary")

# Exit codes — the caller (run_daily_summaries.py) distinguishes these so that
# "ran fine but had nothing to say" is never reported as a plain success.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING_TO_SEND = 2

# Telegram hard limit for a text message.
TELEGRAM_MAX_MESSAGE_CHARS = 4096
TELEGRAM_MAX_RETRIES = 4
OPENAI_MAX_RETRIES = 3

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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what data is available without calling OpenAI or posting to Telegram"
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
        sys.exit(EXIT_ERROR)

    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        logger.error(f"Invalid date format '{date_str}': {e}")
        sys.exit(EXIT_ERROR)

    # Create datetime objects at day boundaries in local timezone
    start_local = datetime.combine(date_obj, dt_time(0, 0, 0), tzinfo=local_tz)
    end_local = datetime.combine(date_obj, dt_time(23, 59, 59), tzinfo=local_tz)

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

    all_streams = session.exec(select(Stream)).all()
    logger.info(
        f"Found {len(streams)} enabled streams out of {len(all_streams)} configured: "
        f"{[s.name for s in streams]}"
    )
    disabled = [s.name for s in all_streams if not s.enabled]
    if disabled:
        logger.info(f"Disabled streams (excluded from the summary): {disabled}")

    return streams


def log_stream_data_diagnostics(
    session: Session,
    stream: Stream,
    start_utc: datetime,
    end_utc: datetime,
) -> Dict[str, int]:
    """
    Log exactly what the DB holds for this stream in the requested window.

    The summary only uses recordings that are classified as "speech" AND carry a
    transcript. When the bot goes quiet, these counters say which stage broke:
    no recordings at all (capture), recordings but none classified (classifier),
    speech but no transcripts (ASR).

    Returns:
        Dict of counters
    """
    def _count(*conditions) -> int:
        statement = select(func.count()).select_from(Recording).where(
            Recording.stream_id == stream.id,
            Recording.start_ts >= start_utc,
            Recording.start_ts <= end_utc,
            *conditions,
        )
        return session.exec(statement).one()

    counters = {
        "total": _count(),
        "unclassified": _count(Recording.classification.is_(None)),
        "speech": _count(Recording.classification == "speech"),
        "music": _count(Recording.classification == "music"),
        "ad": _count(Recording.classification == "ad"),
        "speech_transcribed": _count(
            Recording.classification == "speech",
            Recording.transcript.is_not(None),
        ),
    }

    logger.info(
        "  Data for %s in window: recordings=%d (unclassified=%d, speech=%d, "
        "music=%d, ad=%d), usable transcripts=%d",
        stream.name,
        counters["total"],
        counters["unclassified"],
        counters["speech"],
        counters["music"],
        counters["ad"],
        counters["speech_transcribed"],
    )

    if counters["total"] == 0:
        logger.warning(
            "  NO RECORDINGS at all for %s in this window — capture side problem "
            "(stream status=%s, last_up=%s, last_error=%s)",
            stream.name,
            stream.current_status,
            stream.last_up,
            stream.last_error,
        )
    elif counters["speech"] == 0 and counters["unclassified"] > 0:
        logger.warning(
            "  %d recording(s) for %s were never classified — the watcher's "
            "classifier is not keeping up or is failing",
            counters["unclassified"],
            stream.name,
        )
    elif counters["speech"] > 0 and counters["speech_transcribed"] == 0:
        logger.warning(
            "  %d speech recording(s) for %s but ZERO transcripts — ASR is failing "
            "or has not run yet",
            counters["speech"],
            stream.name,
        )

    return counters


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
        "- CRITICAL: Keep the ENTIRE summary under 4000 characters total",
        "- Be concise - prioritize the most important topics if needed to stay within the character limit",
        "- Balance size of topics, high priority topics should be more detailed, lower priority topics can be shorter",
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
        "Return ONLY the summary paragraphs (one paragraph per topic). Separate each paragraph with a blank line. No heading, no station name, just the summary paragraphs.",
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


def call_openai(prompt: str, stream_name: str) -> str:
    """
    Call OpenAI Chat Completions API.

    Args:
        prompt: Complete prompt text
        stream_name: Station the prompt belongs to (for log context)

    Returns:
        LLM response text

    Raises:
        RuntimeError: If the API cannot be reached or returns an unusable answer
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable not set — no summaries can be generated. "
            "Note that cron jobs do NOT inherit the container environment; the key must be "
            "written into the cron file (see start.sh)."
        )

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    model = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5-mini")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    logger.info(
        "Calling OpenAI for %s (model=%s, prompt=%d chars)",
        stream_name,
        model,
        len(prompt),
    )

    last_error: Optional[str] = None

    for attempt in range(1, OPENAI_MAX_RETRIES + 1):
        started = time.monotonic()
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            elapsed = time.monotonic() - started

            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                logger.warning(
                    "OpenAI attempt %d/%d for %s failed with %s after %.1fs — retrying",
                    attempt,
                    OPENAI_MAX_RETRIES,
                    stream_name,
                    response.status_code,
                    elapsed,
                )
                time.sleep(min(2 ** attempt, 30))
                continue

            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            elapsed = time.monotonic() - started
            last_error = str(e)
            body = ""
            if getattr(e, "response", None) is not None:
                body = e.response.text[:500]
                logger.error("OpenAI response body: %s", body)
            logger.warning(
                "OpenAI attempt %d/%d for %s failed after %.1fs: %s",
                attempt, OPENAI_MAX_RETRIES, stream_name, elapsed, e,
            )
            if attempt < OPENAI_MAX_RETRIES:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise RuntimeError(f"OpenAI request failed for {stream_name}: {last_error}") from e

        try:
            result = response.json()
            choice = result["choices"][0]
            summary_text = choice["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            logger.error("Failed to parse OpenAI response for %s: %s", stream_name, e)
            logger.error("Raw response: %s", response.text[:2000])
            raise RuntimeError(f"Unparseable OpenAI response for {stream_name}") from e

        usage = result.get("usage", {})
        finish_reason = choice.get("finish_reason")
        logger.info(
            "OpenAI OK for %s in %.1fs: %d chars, finish_reason=%s, tokens(prompt/completion/total)=%s/%s/%s",
            stream_name,
            elapsed,
            len(summary_text or ""),
            finish_reason,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )

        if finish_reason == "length":
            logger.warning(
                "OpenAI truncated the summary for %s (finish_reason=length) — "
                "the posted text may end mid-sentence",
                stream_name,
            )

        if not summary_text or not summary_text.strip():
            raise RuntimeError(
                f"OpenAI returned an EMPTY summary for {stream_name} "
                f"(finish_reason={finish_reason}) — nothing to post"
            )

        return summary_text

    raise RuntimeError(f"OpenAI request failed for {stream_name}: {last_error}")


def split_for_telegram(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> List[str]:
    """
    Split a message into Telegram-sized chunks, preferring paragraph then line
    boundaries. A single over-long message is rejected by the API with HTTP 400,
    which used to abort the whole run.
    """
    if len(text) <= limit:
        return [text]

    logger.warning(
        "Message is %d chars, over the Telegram limit of %d — splitting into parts",
        len(text),
        limit,
    )

    chunks: List[str] = []
    remaining = text

    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at <= 0:
            split_at = window.rfind("\n")
        if split_at <= 0:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = limit

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    logger.info("Split into %d parts of sizes %s", len(chunks), [len(c) for c in chunks])
    return chunks


async def verify_telegram_target(bot: Bot, channel_id: str) -> bool:
    """
    Check the token and the channel before doing any work.

    A revoked token or a bot removed from the channel is a very common cause of
    "the bot stopped posting" and produced no useful log line before.
    """
    try:
        me = await bot.get_me()
        logger.info("Telegram bot authenticated: @%s (id=%s)", me.username, me.id)
    except TelegramAPIError as e:
        logger.error("Telegram token check FAILED (%s): %s", type(e).__name__, e)
        return False

    try:
        chat = await bot.get_chat(channel_id)
        logger.info(
            "Telegram target reachable: %s (type=%s, title=%r)",
            channel_id,
            chat.type,
            getattr(chat, "title", None),
        )
    except TelegramForbiddenError as e:
        logger.error(
            "Telegram target %s is FORBIDDEN — the bot was most likely removed from the "
            "channel or lost posting rights: %s",
            channel_id,
            e,
        )
        return False
    except TelegramAPIError as e:
        logger.error(
            "Cannot access Telegram target %s (%s): %s — check the channel id",
            channel_id,
            type(e).__name__,
            e,
        )
        return False

    return True


async def send_single_message(bot: Bot, channel_id: str, text: str, label: str) -> bool:
    """
    Send one message with retries and a plain-text fallback.

    Returns:
        True if the message was delivered
    """
    parse_mode: Optional[str] = "Markdown"

    for attempt in range(1, TELEGRAM_MAX_RETRIES + 1):
        try:
            await bot.send_message(chat_id=channel_id, text=text, parse_mode=parse_mode)
            logger.info(
                "Posted %s to %s (%d chars, parse_mode=%s, attempt %d)",
                label, channel_id, len(text), parse_mode, attempt,
            )
            return True

        except TelegramRetryAfter as e:
            wait = getattr(e, "retry_after", 5)
            logger.warning(
                "Telegram rate-limited %s; retrying in %ss (attempt %d/%d)",
                label, wait, attempt, TELEGRAM_MAX_RETRIES,
            )
            await asyncio.sleep(wait)

        except TelegramBadRequest as e:
            message = str(e)
            logger.error(
                "Telegram rejected %s (attempt %d/%d): %s",
                label, attempt, TELEGRAM_MAX_RETRIES, message,
            )
            if parse_mode and "parse" in message.lower():
                # LLM output regularly contains stray *, _ or [ that legacy
                # Markdown cannot parse. Better to post plain text than nothing.
                logger.warning(
                    "Retrying %s without Markdown formatting (parse error on LLM output)",
                    label,
                )
                parse_mode = None
                continue
            if "too long" in message.lower():
                logger.error(
                    "%s is %d chars — above the Telegram limit; it should have been split",
                    label, len(text),
                )
            return False

        except TelegramForbiddenError as e:
            logger.error(
                "Telegram refused %s: bot has no access to %s (removed from channel?): %s",
                label, channel_id, e,
            )
            return False

        except TelegramNetworkError as e:
            logger.warning(
                "Telegram network error on %s (attempt %d/%d): %s",
                label, attempt, TELEGRAM_MAX_RETRIES, e,
            )
            await asyncio.sleep(min(2 ** attempt, 30))

        except Exception as e:
            logger.error(
                "Unexpected error sending %s (attempt %d/%d): %s",
                label, attempt, TELEGRAM_MAX_RETRIES, e,
                exc_info=True,
            )
            await asyncio.sleep(min(2 ** attempt, 30))

    logger.error("Giving up on %s after %d attempts", label, TELEGRAM_MAX_RETRIES)
    return False


async def post_to_telegram(bot: Bot, text: str, channel_id: str, label: str) -> bool:
    """
    Post a (possibly over-long) message to a Telegram channel.

    Returns:
        True if every part was delivered
    """
    chunks = split_for_telegram(text)
    all_ok = True

    for index, chunk in enumerate(chunks, start=1):
        part_label = label if len(chunks) == 1 else f"{label} part {index}/{len(chunks)}"
        ok = await send_single_message(bot, channel_id, chunk, part_label)
        all_ok = all_ok and ok

    return all_ok


def collect_stream_summaries(
    args: argparse.Namespace,
    start_utc: datetime,
    end_utc: datetime,
) -> Tuple[List[Dict], int]:
    """
    Build a summary per stream that has usable transcripts.

    Returns:
        (summaries, failed_stream_count)
    """
    stream_summaries: List[Dict] = []
    failures = 0

    with Session(engine) as session:
        streams = fetch_enabled_streams(session)

        if not streams:
            logger.error(
                "No enabled streams found — nothing can be summarised. "
                "Enable at least one stream in the dashboard."
            )
            return [], 0

        for stream in streams:
            logger.info(f"Processing stream: {stream.name}")

            counters = log_stream_data_diagnostics(session, stream, start_utc, end_utc)

            recordings = fetch_recordings_for_stream(
                session, stream.id, start_utc, end_utc
            )

            if not recordings:
                logger.warning(
                    "  SKIPPING %s: no speech recording with a transcript in the window "
                    "(recordings in window=%d)",
                    stream.name,
                    counters["total"],
                )
                continue

            transcriptions = [r.transcript for r in recordings]
            total_chars = sum(len(t or "") for t in transcriptions)
            logger.info(
                "  Using %d recording(s) for %s, %d transcript chars total "
                "(first=%s, last=%s)",
                len(recordings),
                stream.name,
                total_chars,
                recordings[0].start_ts,
                recordings[-1].start_ts,
            )

            if total_chars == 0:
                logger.warning(
                    "  SKIPPING %s: transcripts exist but are all empty", stream.name
                )
                continue

            prompt = build_llm_prompt_for_stream(
                stream.name,
                stream.language,
                transcriptions,
                args.target_language
            )

            if args.dry_run:
                logger.info(
                    "  DRY RUN: would call OpenAI for %s with a %d-char prompt",
                    stream.name,
                    len(prompt),
                )
                continue

            try:
                summary = call_openai(prompt, stream.name)
            except RuntimeError as e:
                # One failing station must not silence the others.
                failures += 1
                logger.error("  Summary generation FAILED for %s: %s", stream.name, e)
                continue

            stream_summaries.append({
                "name": stream.name,
                "summary": summary.strip()
            })

            logger.info(f"  Summary generated for {stream.name}")

    return stream_summaries, failures


async def main() -> int:
    """Main execution function. Returns a process exit code."""
    args = parse_args()
    run_started = time.monotonic()

    logger.info("=== Daily Radio Summary Script ===")
    logger.info(
        "Host: %s | pid: %s | now(UTC): %s",
        socket.gethostname(),
        os.getpid(),
        datetime.utcnow().isoformat(timespec="seconds"),
    )
    logger.info(f"Date: {args.date}")
    logger.info(f"Timezone: {args.timezone}")
    logger.info(f"Target language: {args.target_language}")
    logger.info(f"Telegram channel: {args.telegram_channel_id}")
    logger.info(f"Database: {os.getenv('DATABASE_URL', '<default>')}")
    logger.info(f"OPENAI_API_KEY present: {bool(os.getenv('OPENAI_API_KEY'))}")
    if args.dry_run:
        logger.info("DRY RUN: no OpenAI calls, no Telegram messages will be sent")

    # Compute UTC time range
    start_utc, end_utc = compute_utc_range(args.date, args.timezone)

    stream_summaries, failures = collect_stream_summaries(args, start_utc, end_utc)

    if args.dry_run:
        logger.info("=== Dry run finished in %.1fs ===", time.monotonic() - run_started)
        return EXIT_OK

    # Check if we have any summaries
    if not stream_summaries:
        logger.error(
            "NOTHING TO SEND: no stream produced a summary for %s "
            "(%d stream(s) failed during generation). No Telegram message will be posted.",
            args.date,
            failures,
        )
        return EXIT_ERROR if failures else EXIT_NOTHING_TO_SEND

    bot = Bot(token=args.telegram_bot_token)
    posted = 0
    attempted = 0

    try:
        # Advisory only: a failed check is logged loudly but we still try to post,
        # so the check can never be the reason a message is withheld.
        if not await verify_telegram_target(bot, args.telegram_channel_id):
            logger.warning(
                "Telegram pre-flight check failed — attempting to post %d summary/summaries anyway",
                len(stream_summaries),
            )

        # Send first message with intro and first station
        first_message = "\n".join([
            get_summary_intro(args.target_language),
            "",
            f"*{stream_summaries[0]['name']}* — {stream_summaries[0]['summary']}",
        ])

        attempted += 1
        if await post_to_telegram(
            bot, first_message, args.telegram_channel_id,
            f"intro + {stream_summaries[0]['name']}",
        ):
            posted += 1

        # Send remaining stations as separate messages. A failure on one station
        # no longer aborts the rest of the run.
        for item in stream_summaries[1:]:
            message = f"*{item['name']}* — {item['summary']}"
            attempted += 1
            if await post_to_telegram(bot, message, args.telegram_channel_id, item["name"]):
                posted += 1
    finally:
        await bot.session.close()

    elapsed = time.monotonic() - run_started
    logger.info(
        "=== Finished in %.1fs: %d/%d message(s) posted to %s, %d stream(s) failed to summarise ===",
        elapsed, posted, attempted, args.telegram_channel_id, failures,
    )

    if posted == 0:
        logger.error("No message reached Telegram — the channel got nothing today")
        return EXIT_ERROR
    if posted < attempted or failures:
        logger.warning("Partial delivery: %d of %d message(s) posted", posted, attempted)
        return EXIT_ERROR

    logger.info("=== Script completed successfully ===")
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception:
        logger.exception("Daily summary crashed with an unhandled exception")
        sys.exit(EXIT_ERROR)
