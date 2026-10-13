"""Local accounts: password hashing, sign-in sessions and roles.

Passwords are stored as PBKDF2-SHA256 (salted, 240k iterations).  A sign-in
creates a random cookie token; only its SHA-256 is kept in ``auth_sessions``,
so a copied database cannot be used to sign in.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import secrets
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from storage.db_models import AuthSession, User

ROLES = ("PROCTOR", "CHIEF", "ADMIN")
ROLE_RANK = {"PROCTOR": 1, "CHIEF": 2, "ADMIN": 3}
SESSION_HOURS = 12
_ITERATIONS = 240_000
MIN_PASSWORD_LENGTH = 8


class AuthError(ValueError):
    """Invalid credentials or an invalid account change."""


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def create_user(db: Session, username: str, display_name: str, role: str, password: str) -> User:
    username = normalize_username(username)
    role = (role or "").upper()
    if not username or len(username) > 64 or not all(c.isalnum() or c in "._-" for c in username):
        raise AuthError("Username: letters, digits, '.', '_' or '-' only (max 64).")
    if role not in ROLES:
        raise AuthError(f"Role must be one of {', '.join(ROLES)}.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must have at least {MIN_PASSWORD_LENGTH} characters.")
    if db.query(User).filter(User.username == username).first() is not None:
        raise AuthError("This username is taken.")
    user = User(
        username=username,
        display_name=(display_name or username).strip()[:120] or username,
        role=role,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_password(db: Session, user: User, password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must have at least {MIN_PASSWORD_LENGTH} characters.")
    user.password_hash = hash_password(password)
    # Existing sign-ins end when the password changes
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
    db.commit()


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == normalize_username(username)).first()
    # Same message for unknown user and wrong password
    if user is None or not user.is_active or not verify_password(password or "", user.password_hash):
        raise AuthError("Wrong username or password.")
    return user


def start_session(db: Session, user: User) -> Tuple[str, datetime.datetime]:
    token = secrets.token_urlsafe(32)
    now = datetime.datetime.utcnow()
    expires = now + datetime.timedelta(hours=SESSION_HOURS)
    db.query(AuthSession).filter(AuthSession.expires_at < now).delete(synchronize_session=False)
    db.add(AuthSession(user_id=user.id, token_hash=_token_hash(token), expires_at=expires))
    user.last_login_at = now
    db.commit()
    return token, expires


def user_for_token(db: Session, token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    row = db.query(AuthSession).filter(AuthSession.token_hash == _token_hash(token)).first()
    if row is None or row.expires_at < datetime.datetime.utcnow():
        return None
    user = db.get(User, row.user_id)
    return user if user is not None and user.is_active else None


def end_session(db: Session, token: Optional[str]) -> None:
    if token:
        db.query(AuthSession).filter(AuthSession.token_hash == _token_hash(token)).delete(synchronize_session=False)
        db.commit()


def has_role(user: Optional[User], *roles: str) -> bool:
    """True when the user's role is at least the lowest of ``roles`` (ADMIN ⊃ CHIEF ⊃ PROCTOR)."""
    if user is None:
        return False
    return ROLE_RANK.get(user.role, 0) >= min(ROLE_RANK.get(r, 99) for r in roles)


def user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }
