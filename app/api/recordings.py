import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, desc, select

from app.api.auth import get_current_admin_user, get_current_user
from app.core.db import get_session
from app.models.models import Recording, User

router = APIRouter()

@router.get("/", response_model=List[Recording])
def read_recordings(skip: int = 0, limit: int = 100, stream_id: int = None, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    query = select(Recording).where(Recording.status != "deleted")
    if stream_id:
        query = query.where(Recording.stream_id == stream_id)
    query = query.order_by(desc(Recording.start_ts)).offset(skip).limit(limit)
    recordings = session.exec(query).all()
    return recordings

@router.get("/{recording_id}/download")
def download_recording(recording_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    recording = session.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    if recording.status == "deleted":
        raise HTTPException(status_code=404, detail="Recording has been deleted")
    
    if not os.path.exists(recording.path):
        raise HTTPException(status_code=404, detail="File not found on disk")
        
    return FileResponse(recording.path, filename=os.path.basename(recording.path), media_type="audio/wav")
