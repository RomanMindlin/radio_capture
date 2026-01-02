from datetime import date, datetime, time, timedelta
from typing import List, Optional, Sequence

from sqlmodel import Session, delete, select

from app.core.db import engine
from app.models.models import Recording, SpeechBlock


def _calculate_end_ts(recording: Recording) -> datetime:
    """
    Resolve an end timestamp for a recording. Falls back to duration when end_ts is missing.
    """
    if recording.end_ts:
        return recording.end_ts
    if recording.duration_seconds and recording.duration_seconds > 0:
        return recording.start_ts + timedelta(seconds=recording.duration_seconds)
    return recording.start_ts


def _day_bounds(target_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time.min)
    end = datetime.combine(target_date, time.max)
    return start, end


def build_speech_blocks(
    station_id: int,
    target_date: date,
    gap_threshold: int = 5,
    min_duration: int = 60,
    session: Optional[Session] = None,
) -> List[SpeechBlock]:
    """
    Builds and persists speech blocks for a given station (stream) and day.
    Idempotent: removes existing blocks for that day before inserting new ones.
    """
    owns_session = session is None
    if owns_session:
        session = Session(engine)

    blocks: List[SpeechBlock] = []
    try:
        day_start, day_end = _day_bounds(target_date)

        # Delete existing blocks for the day to keep operation idempotent.
        session.exec(
            delete(SpeechBlock).where(
                SpeechBlock.stream_id == station_id,
                SpeechBlock.start_ts >= day_start,
                SpeechBlock.start_ts <= day_end,
            )
        )
        session.commit()

        # Load all chunks (speech + non-speech) ordered by start time.
        recordings: Sequence[Recording] = session.exec(
            select(Recording)
            .where(
                Recording.stream_id == station_id,
                Recording.start_ts >= day_start,
                Recording.start_ts <= day_end,
            )
            .order_by(Recording.start_ts)
        ).all()

        current_chunk_ids: List[int] = []
        current_texts: List[str] = []
        block_start: Optional[datetime] = None
        block_end: Optional[datetime] = None

        def finalize_block():
            nonlocal current_chunk_ids, current_texts, block_start, block_end
            if not current_chunk_ids or not block_start or not block_end:
                return

            duration = (block_end - block_start).total_seconds()
            if duration < min_duration:
                current_chunk_ids, current_texts, block_start, block_end = [], [], None, None
                return

            text_blob = "\n".join([t for t in current_texts if t])
            block = SpeechBlock(
                stream_id=station_id,
                start_ts=block_start,
                end_ts=block_end,
                duration_seconds=duration,
                chunk_ids=list(current_chunk_ids),
                text=text_blob,
            )
            session.add(block)
            blocks.append(block)

            current_chunk_ids, current_texts, block_start, block_end = [], [], None, None

        for rec in recordings:
            rec_end = _calculate_end_ts(rec)
            if rec.classification != "speech":
                finalize_block()
                continue

            if not current_chunk_ids:
                block_start = rec.start_ts
                block_end = rec_end
                current_chunk_ids = [rec.id]
                current_texts = [rec.transcript or ""]
                continue

            gap = (rec.start_ts - block_end).total_seconds()
            if gap <= gap_threshold:
                current_chunk_ids.append(rec.id)
                current_texts.append(rec.transcript or "")
                if rec_end > block_end:
                    block_end = rec_end
            else:
                finalize_block()
                block_start = rec.start_ts
                block_end = rec_end
                current_chunk_ids = [rec.id]
                current_texts = [rec.transcript or ""]

        finalize_block()
        session.commit()

        for block in blocks:
            session.refresh(block)
        return blocks
    finally:
        if owns_session:
            session.close()
