import csv
import io
import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, desc, select

from app.api.auth import get_current_user
from app.core.db import get_session
from app.models.models import Recording, Stream, User, UserRole
from app.services.stats import get_detailed_stats

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/summary")
async def get_stats_summary(
    days: int = 30,
    current_user: User = Depends(get_current_user)
):
    """
    Get aggregated stats for all streams.
    """
    return get_detailed_stats(days=days)

@router.get("/files")
async def list_files(
    stream_id: Optional[int] = None,
    date_from: Optional[str] = None, # YYYY-MM-DD
    date_to: Optional[str] = None,   # YYYY-MM-DD
    skip: int = 0,
    limit: int = 50,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    List recordings with filtering.
    """
    from sqlalchemy.orm import joinedload
    query = (
        select(Recording)
        .options(joinedload(Recording.stream))
        .where(Recording.status != "deleted")
        .order_by(desc(Recording.start_ts))
    )
    
    if stream_id:
        query = query.where(Recording.stream_id == stream_id)
    if date_from:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        query = query.where(Recording.start_ts >= dt_from)
    if date_to:
        dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        query = query.where(Recording.start_ts <= dt_to)
        
    results = session.exec(query.offset(skip).limit(limit)).all()
    
    response_data = []
    for r in results:
        response_data.append({
            "id": r.id,
            "stream_id": r.stream_id,
            "stream": {"name": r.stream.name} if r.stream else None,
            "path": r.path,
            "start_ts": r.start_ts.isoformat(),
            "end_ts": r.end_ts.isoformat() if r.end_ts else None,
            "size_bytes": r.size_bytes,
            "duration_seconds": r.duration_seconds,
            "status": r.status,
            "classification": r.classification
        })
    
    return response_data

@router.get("/files/export")
async def export_files_csv(
    stream_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Export filtered recordings to CSV.
    """
    from sqlalchemy.orm import joinedload
    query = (
        select(Recording)
        .options(joinedload(Recording.stream))
        .where(Recording.status != "deleted")
        .order_by(desc(Recording.start_ts))
    )
    
    if stream_id: query = query.where(Recording.stream_id == stream_id)
    if date_from: 
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        query = query.where(Recording.start_ts >= dt_from)
    if date_to:
        dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        query = query.where(Recording.start_ts <= dt_to)
        
    results = session.exec(query).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Stream", "Start Time", "Duration (s)", "Size (Bytes)", "Path", "Status"])
    
    for r in results:
        stream_name = r.stream.name if r.stream else "Unknown"
        writer.writerow([
            r.id,
            stream_name,
            r.start_ts.isoformat(),
            r.duration_seconds,
            r.size_bytes,
            r.path,
            r.status
        ])
        
    output.seek(0)
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=recordings_export.csv"})

@router.get("/files/{file_id}/download")
async def download_file(
    file_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Secure file conversation.
    """
    recording = session.get(Recording, file_id)
    if not recording:
        raise HTTPException(status_code=404, detail="File not found")
    
    if recording.status == "deleted":
        raise HTTPException(status_code=404, detail="Recording has been deleted")
    
    if not os.path.exists(recording.path):
        raise HTTPException(status_code=404, detail="File descriptor exists but file missing on disk")
        
    filename = os.path.basename(recording.path)
    return FileResponse(recording.path, filename=filename)

@router.get("/files/{file_id}/stream")
async def stream_file(
    file_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Stream audio file for in-browser playback.
    """
    recording = session.get(Recording, file_id)
    if not recording:
        raise HTTPException(status_code=404, detail="File not found")
    
    if recording.status == "deleted":
        raise HTTPException(status_code=404, detail="Recording has been deleted")
    
    if not os.path.exists(recording.path):
        raise HTTPException(status_code=404, detail="File descriptor exists but file missing on disk")
    
    # Determine media type based on file extension
    file_ext = os.path.splitext(recording.path)[1].lower()
    media_type_map = {
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.ogg': 'audio/ogg',
        '.m4a': 'audio/mp4',
        '.aac': 'audio/aac',
        '.flac': 'audio/flac'
    }
    media_type = media_type_map.get(file_ext, 'audio/mpeg')
    
    return FileResponse(recording.path, media_type=media_type)

@router.post("/files/{file_id}/classify")
async def classify_file(
    file_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Classify audio file as speech, music, or ad using PANNs CNN14 model.
    """
    from app.services.audio_classifier import classify_audio
    
    # Get recording from database
    recording = session.get(Recording, file_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # Check if file exists on disk
    if not os.path.exists(recording.path):
        raise HTTPException(
            status_code=404, 
            detail="File descriptor exists but file missing on disk"
        )
    
    try:
        # Run classification
        classification = classify_audio(recording.path)
        
        # Update database
        recording.classification = classification
        session.add(recording)
        session.commit()
        session.refresh(recording)
        
        # Return updated metadata
        return {
            "id": recording.id,
            "path": recording.path,
            "classification": recording.classification,
            "stream_id": recording.stream_id,
            "start_ts": recording.start_ts.isoformat(),
            "status": recording.status,
            "message": f"Successfully classified as '{classification}'"
        }
        
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Don't overwrite existing classification on error
        raise HTTPException(
            status_code=500,
            detail=f"Classification failed: {str(e)}"
        )

@router.post("/files/{file_id}/asr")
async def transcribe_file(
    file_id: int,
    model: str = "tiny",
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Transcribe audio file using Whisper ASR.
    Returns transcription with timestamps and metadata.
    """
    import time
    from app.services.asr import transcribe
    
    # Get recording from database
    recording = session.get(Recording, file_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    # Check if file exists on disk
    if not os.path.exists(recording.path):
        raise HTTPException(
            status_code=404,
            detail="File not found on disk"
        )
    
    # Check cache: if already transcribed with same model, return cached result
    model_id = f"whisper-{model}"
    if recording.asr_ts and recording.asr_model == model_id:
        logger.info(f"Returning cached transcription for recording {file_id}")
        return {
            "recording_id": recording.id,
            "transcript": recording.transcript,
            "segments": recording.transcript_json.get("segments", []) if recording.transcript_json else [],
            "model": recording.asr_model,
            "confidence": recording.asr_confidence or 0.0,
            "processing_seconds": 0.0,  # Cached result
            "cached": True
        }
    
    # Run transcription
    start_time = time.time()
    try:
        # Get language from stream configuration
        stream_language = recording.stream.language if recording.stream else "he"
        logger.info(f"Starting ASR for recording {file_id} with model {model}, language {stream_language}")
        result = transcribe(recording.path, model=model, language=stream_language)
        processing_time = time.time() - start_time
        
        # Update database with results
        recording.transcript = result["transcript"]
        recording.transcript_json = {
            "segments": result["segments"]
        }
        recording.asr_model = result["model"]
        recording.asr_confidence = result["confidence"]
        recording.asr_ts = datetime.utcnow()
        
        session.add(recording)
        session.commit()
        session.refresh(recording)
        
        logger.info(f"ASR completed for recording {file_id} in {processing_time:.2f}s")
        
        # Return response
        return {
            "recording_id": recording.id,
            "transcript": result["transcript"],
            "segments": result["segments"],
            "model": result["model"],
            "confidence": result["confidence"],
            "processing_seconds": round(processing_time, 2),
            "cached": False
        }
        
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"ASR failed for recording {file_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"ASR failed: {str(e)}"
        )

