"""Sign-in, first-run setup and account management."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import COOKIE_NAME, auth_mode, current_user, require_roles
from storage.auth_service import (
    AuthError,
    ROLES,
    SESSION_HOURS,
    authenticate,
    create_user,
    end_session,
    set_password,
    start_session,
    user_to_dict,
)
from storage.database import get_db
from storage.db_models import AuditLog, User

router = APIRouter(tags=["Accounts"])


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    display_name: str = ""
    password: str


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    role: str = "PROCTOR"
    password: str


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, description="New password (signs the user out everywhere)")


def _audit(db: Session, actor: str, action: str, resource_id: str, details: Dict[str, Any]) -> None:
    db.add(AuditLog(actor_id=actor[:100], action=action, resource_type="USER", resource_id=resource_id,
                    metadata_json=json.dumps(details)))
    db.commit()


def _sign_in(request: Request, response: Response, db: Session, user: User) -> None:
    token, _expires = start_session(db, user)
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax",
        secure=request.url.scheme == "https", path="/",
    )


@router.get("/auth/me")
def me(user: Optional[User] = Depends(current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {
        "user": user_to_dict(user) if user else None,
        "mode": auth_mode(),
        "needs_setup": db.query(User).count() == 0,
        "roles": list(ROLES),
    }


@router.post("/auth/setup")
def first_admin(payload: SetupRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Create the first administrator; only possible while there are no accounts."""
    if db.query(User).count() > 0:
        raise HTTPException(status_code=409, detail="Accounts already exist. Sign in instead.")
    try:
        user = create_user(db, payload.username, payload.display_name, "ADMIN", payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(db, user.username, "CREATE_FIRST_ADMIN", user.id, {"username": user.username})
    _sign_in(request, response, db, user)
    return {"user": user_to_dict(user)}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        user = authenticate(db, payload.username, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _sign_in(request, response, db, user)
    _audit(db, user.username, "LOGIN", user.id, {})
    return {"user": user_to_dict(user)}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Dict[str, Any]:
    end_session(db, request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/users")
def list_users(db: Session = Depends(get_db), _admin=Depends(require_roles("ADMIN", always=True))) -> List[Dict[str, Any]]:
    return [user_to_dict(u) for u in db.query(User).order_by(User.role.desc(), User.username).all()]


@router.get("/users/directory")
def user_directory(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """Active people (name and role only), for choosing a session's proctor."""
    return [
        {"id": u.id, "display_name": u.display_name, "username": u.username, "role": u.role}
        for u in db.query(User).filter(User.is_active == True).order_by(User.display_name).all()  # noqa: E712
    ]


@router.post("/users", status_code=201)
def add_user(payload: UserCreate, db: Session = Depends(get_db),
             admin: User = Depends(require_roles("ADMIN", always=True))) -> Dict[str, Any]:
    try:
        user = create_user(db, payload.username, payload.display_name, payload.role, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(db, admin.username, "CREATE_USER", user.id, {"username": user.username, "role": user.role})
    return user_to_dict(user)


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, db: Session = Depends(get_db),
                admin: User = Depends(require_roles("ADMIN", always=True))) -> Dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    changes: Dict[str, Any] = {}
    if payload.role is not None:
        role = payload.role.upper()
        if role not in ROLES:
            raise HTTPException(status_code=400, detail=f"Role must be one of {', '.join(ROLES)}.")
        changes["role"] = role
    if payload.is_active is not None:
        changes["is_active"] = payload.is_active
    # Never lock the last administrator out
    if user.role == "ADMIN" and (changes.get("role", "ADMIN") != "ADMIN" or changes.get("is_active") is False):
        admins = db.query(User).filter(User.role == "ADMIN", User.is_active == True).count()  # noqa: E712
        if admins <= 1:
            raise HTTPException(status_code=409, detail="Keep at least one active administrator.")
    for key, value in changes.items():
        setattr(user, key, value)
    if payload.display_name is not None and payload.display_name.strip():
        user.display_name = payload.display_name.strip()[:120]
        changes["display_name"] = user.display_name
    db.commit()
    if payload.password:
        try:
            set_password(db, user, payload.password)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        changes["password"] = "changed"
    _audit(db, admin.username, "UPDATE_USER", user.id, changes)
    db.refresh(user)
    return user_to_dict(user)
