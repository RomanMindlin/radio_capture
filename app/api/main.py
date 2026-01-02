import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from app.api import auth, recordings, speech_blocks, stats_routes, streams, ui_routes, users
from app.api.auth import get_password_hash
from app.core.db import create_db_and_tables, engine, get_session
from app.models.models import User, UserRole
from app.services.stream_manager import manager
from app.services.watcher import watcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Radio Stream Capture Service")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(streams.router, prefix="/api/streams", tags=["streams"])
app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
app.include_router(users.router, prefix="/api/users", tags=["users"])

app.include_router(stats_routes.router, prefix="/api/stats", tags=["stats"])
app.include_router(speech_blocks.router, prefix="/api", tags=["speech_blocks"])

app.include_router(ui_routes.router)

@app.on_event("startup")
async def on_startup():
    create_db_and_tables()
    
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.username == "admin")).first()
        if not admin:
            logger.info("Creating default admin user...")
            hashed_pw = get_password_hash("admin")
            admin_user = User(username="admin", password_hash=hashed_pw, role=UserRole.ADMIN)
            session.add(admin_user)
            session.commit()
    
    await manager.start()
    await watcher.start()

@app.on_event("shutdown")
async def on_shutdown():
    await manager.stop()

@app.get("/")
def root():
    return RedirectResponse(url="/dashboard")
