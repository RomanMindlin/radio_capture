from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, desc, select

from app.api.auth import get_current_user
from app.core.db import get_session
from app.models.models import Recording, Stream, User, UserRole

templates = Jinja2Templates(directory="app/templates")
router = APIRouter()

# Helper to inject user into template context
async def get_optional_user(request: Request):
    try:
        if "access_token" in request.cookies:
            # We manually reuse the logic from auth.get_current_user but relaxed
            # Ideally we reuse the dependency but it raises 401.
            # For pages, if 401, we redirect to login.
            return None 
    except:
        pass
    return None

# We use a wrapper or middleware approach for page auth usually
# Or simple dependency that redirects on failure
async def login_required(request: Request, session: Session = Depends(get_session)):
    try:
        user = await get_current_user(request, session)
        return user
    except HTTPException:
        return None 

@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.get("/dashboard")
async def dashboard(request: Request, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    streams = session.exec(select(Stream)).all()
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user, "streams": streams})

@router.get("/stats")
async def stats_page(request: Request, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    streams = session.exec(select(Stream)).all() # For filter dropdowns if needed
    return templates.TemplateResponse("stats.html", {"request": request, "user": user, "streams": streams})

@router.get("/streams")
async def streams_page(request: Request, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    streams = session.exec(select(Stream)).all()
    return templates.TemplateResponse("streams.html", {"request": request, "user": user, "streams": streams})

@router.get("/streams/new")
async def new_stream_page(request: Request, user: User = Depends(login_required)):
    if not user: return RedirectResponse("/login")
    if user.role != UserRole.ADMIN: return RedirectResponse("/dashboard")
    return templates.TemplateResponse("stream_edit.html", {"request": request, "user": user, "stream": None})

@router.get("/streams/{stream_id}")
async def stream_detail(request: Request, stream_id: int, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    stream = session.get(Stream, stream_id)
    if not stream: return RedirectResponse("/streams")
    
    # Get recent recordings
    recordings = session.exec(
        select(Recording)
        .where(Recording.stream_id == stream.id, Recording.status != "deleted")
        .order_by(desc(Recording.start_ts))
        .limit(20)
    ).all()
    
    return templates.TemplateResponse("stream_detail.html", {"request": request, "user": user, "stream": stream, "recordings": recordings})

@router.get("/streams/{stream_id}/edit")
async def edit_stream_page(request: Request, stream_id: int, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    if user.role != UserRole.ADMIN: return RedirectResponse("/dashboard")
    stream = session.get(Stream, stream_id)
    return templates.TemplateResponse("stream_edit.html", {"request": request, "user": user, "stream": stream})

@router.get("/recordings")
async def recordings_page(request: Request, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    streams = session.exec(select(Stream)).all()
    return templates.TemplateResponse("recordings.html", {"request": request, "user": user, "streams": streams})

@router.get("/settings")
async def settings_page(request: Request, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    users_list = []
    if user.role == UserRole.ADMIN:
        users_list = session.exec(select(User)).all()
    return templates.TemplateResponse("settings.html", {"request": request, "user": user, "users": users_list})

@router.get("/settings/users/new")
async def new_user_page(request: Request, user: User = Depends(login_required)):
    if not user: return RedirectResponse("/login")
    if user.role != UserRole.ADMIN: return RedirectResponse("/dashboard")
    return templates.TemplateResponse("user_edit.html", {"request": request, "user": user, "user_obj": None})

@router.get("/settings/users/{user_id}")
async def edit_user_page(request: Request, user_id: int, user: User = Depends(login_required), session: Session = Depends(get_session)):
    if not user: return RedirectResponse("/login")
    if user.role != UserRole.ADMIN: return RedirectResponse("/dashboard")
    user_obj = session.get(User, user_id)
    return templates.TemplateResponse("user_edit.html", {"request": request, "user": user, "user_obj": user_obj})
