from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.auth import get_current_admin_user, get_password_hash
from app.core.db import get_session
from app.models.models import User, UserRole

router = APIRouter()

class UserCreate(BaseModel):
    username: str
    password: str
    role: UserRole = UserRole.OPERATOR
    active: bool = True

class UserUpdate(BaseModel):
    password: Optional[str] = None
    role: Optional[UserRole] = None
    active: Optional[bool] = None

@router.get("/", response_model=List[User])
def read_users(session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    return session.exec(select(User)).all()

@router.get("/{user_id}", response_model=User)
def read_user(user_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.post("/", response_model=User)
def create_user(user_in: UserCreate, session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    # Check if user exists
    existing = session.exec(select(User).where(User.username == user_in.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_pw = get_password_hash(user_in.password)
    db_user = User(
        username=user_in.username,
        password_hash=hashed_pw,
        role=user_in.role,
        active=user_in.active
    )
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user

@router.put("/{user_id}", response_model=User)
def update_user(user_id: int, user_in: UserUpdate, session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user_in.password and user_in.password.strip():
        db_user.password_hash = get_password_hash(user_in.password)
    if user_in.role is not None:
        db_user.role = user_in.role
    if user_in.active is not None:
        db_user.active = user_in.active
        
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user

@router.delete("/{user_id}")
def delete_user(user_id: int, session: Session = Depends(get_session), current_user: User = Depends(get_current_admin_user)):
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if db_user.id == current_user.id:
         raise HTTPException(status_code=400, detail="Cannot delete yourself")
         
    session.delete(db_user)
    session.commit()
    return {"ok": True}
