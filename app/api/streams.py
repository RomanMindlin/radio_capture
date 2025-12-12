from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.api.auth import get_current_admin_user, get_current_user
from app.core.db import get_session
from app.models.models import Stream, User
from app.services.stream_manager import manager

router = APIRouter()

@router.get("/", response_model=List[Stream])
def read_streams(session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    streams = session.exec(select(Stream)).all()
    return streams

@router.get("/{stream_id}", response_model=Stream)
def read_stream(stream_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    stream = session.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    return stream

@router.post("/", response_model=Stream)
def create_stream(stream: Stream, session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    existing = session.exec(select(Stream).where(Stream.name == stream.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Stream name already exists")
    
    session.add(stream)
    session.commit()
    session.refresh(stream)
    return stream

@router.put("/{stream_id}", response_model=Stream)
async def update_stream(stream_id: int, stream_data: Stream, session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    db_stream = session.get(Stream, stream_id)
    if not db_stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    db_stream.name = stream_data.name
    db_stream.url = stream_data.url
    db_stream.mandatory_params = stream_data.mandatory_params
    db_stream.optional_params = stream_data.optional_params
    db_stream.enabled = stream_data.enabled
    db_stream.language = stream_data.language
    
    session.add(db_stream)
    session.commit()
    session.refresh(db_stream)
    
    return db_stream

@router.delete("/{stream_id}")
async def delete_stream(stream_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    stream = session.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    await manager.stop_stream(stream_id)
    
    session.delete(stream)
    session.commit()
    return {"ok": True}

@router.post("/{stream_id}/start")
async def start_stream_endpoint(stream_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    stream = session.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    stream.enabled = True
    session.add(stream)
    session.commit()
    
    asyncio.create_task(manager.reconcile_streams())
    
    return {"status": "starting"}

@router.post("/{stream_id}/stop")
async def stop_stream_endpoint(stream_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    stream = session.get(Stream, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
        
    stream.enabled = False
    session.add(stream)
    session.commit()
    
    await manager.stop_stream(stream_id)
    return {"status": "stopped"}

import asyncio
