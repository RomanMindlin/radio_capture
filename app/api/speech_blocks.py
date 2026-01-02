from datetime import date, datetime, time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.auth import get_current_user
from app.core.db import get_session
from app.models.models import SpeechBlock, Stream, User
from app.services.speech_blocks import build_speech_blocks

router = APIRouter()


def _day_bounds(target_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time.min)
    end = datetime.combine(target_date, time.max)
    return start, end


class SpeechBlockRebuildRequest(BaseModel):
    date: date


@router.post("/stations/{station_id}/speech-blocks/rebuild")
def rebuild_speech_blocks(
    station_id: int,
    payload: SpeechBlockRebuildRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    stream = session.get(Stream, station_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Station not found")

    blocks = build_speech_blocks(
        station_id=station_id,
        target_date=payload.date,
        session=session,
    )

    total_minutes = int(sum(block.duration_seconds for block in blocks) // 60)
    return {
        "station_id": station_id,
        "date": payload.date.isoformat(),
        "blocks_created": len(blocks),
        "total_speech_minutes": total_minutes,
    }


@router.get("/stations/{station_id}/speech-blocks", response_model=List[SpeechBlock])
def list_speech_blocks(
    station_id: int,
    target_date: date = Query(..., alias="date"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    stream = session.get(Stream, station_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Station not found")

    day_start, day_end = _day_bounds(target_date)
    blocks = session.exec(
        select(SpeechBlock)
        .where(
            SpeechBlock.stream_id == station_id,
            SpeechBlock.start_ts >= day_start,
            SpeechBlock.start_ts <= day_end,
        )
        .order_by(SpeechBlock.start_ts)
    ).all()
    return blocks
