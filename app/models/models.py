from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import Column, String
from sqlmodel import JSON, Field, Relationship, SQLModel


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: UserRole = Field(default=UserRole.OPERATOR)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = Field(default=True)

class Stream(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    url: str
    enabled: bool = Field(default=True)
    
    # JSON fields
    # mandatory_params example: {"format": "mp3", "segment_time": 3600, "channels": 2}
    mandatory_params: dict = Field(default={}, sa_column=Column(JSON))
    
    # optional_params example: {"bitrate": "128k", "retention_days": 30, "retry_delay": 5}
    optional_params: dict = Field(default={}, sa_column=Column(JSON))
    
    last_up: Optional[datetime] = None
    last_error: Optional[str] = None
    current_status: str = Field(default="stopped") # stopped, running, error

    recordings: List["Recording"] = Relationship(back_populates="stream")
    events: List["Event"] = Relationship(back_populates="stream")

class Recording(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id")
    path: str
    start_ts: datetime
    end_ts: Optional[datetime] = None
    size_bytes: int = Field(default=0)
    duration_seconds: float = Field(default=0.0)
    status: str = Field(default="recording") # recording, completed, error
    classification: Optional[str] = Field(default=None) # speech, music, ad

    stream: Optional[Stream] = Relationship(back_populates="recordings")

class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    stream_id: Optional[int] = Field(default=None, foreign_key="stream.id")
    level: str # info, warning, error
    message: str
    ts: datetime = Field(default_factory=datetime.utcnow)

    stream: Optional[Stream] = Relationship(back_populates="events")

class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_token: str
    chat_id: str
    enabled: bool = Field(default=True)
    daily_report_time: Optional[str] = None # HH:MM format
    thresholds: dict = Field(default={}, sa_column=Column(JSON)) 
    # thresholds example: {"disk_min_gb": 5, "error_burst_limit": 10}
