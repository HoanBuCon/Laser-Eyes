"""Who is calling: the signed-in user, sign-in mode and role checks.

``VIGIL_AUTH_MODE``:

* ``optional`` (default): pages and the demo work without signing in, as
  before.  A signed-in user's name is used for review decisions; actions that
  carry formal weight (approving reports, managing accounts) always need a
  signed-in user with the right role.
* ``required``: every page and API needs a signed-in user (see the middleware
  in ``api.main``), and management actions need their role.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from storage.auth_service import has_role, user_for_token
from storage.database import get_db
from storage.db_models import User

COOKIE_NAME = "vigil_session"


def auth_mode() -> str:
    return "required" if os.getenv("VIGIL_AUTH_MODE", "optional").strip().lower() == "required" else "optional"


def current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    return user_for_token(db, request.cookies.get(COOKIE_NAME))


def require_roles(*roles: str, always: bool = False):
    """Dependency: the caller must hold one of ``roles``.

    With ``always=False`` the check applies only in ``required`` mode, so the
    open demo keeps working; ``always=True`` applies in every mode.
    """

    def check(user: Optional[User] = Depends(current_user)) -> Optional[User]:
        if not always and auth_mode() != "required":
            return user
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in to continue.")
        if not has_role(user, *roles):
            raise HTTPException(status_code=403, detail=f"This needs the {' or '.join(roles)} role.")
        return user

    return check


def actor_name(user: Optional[User], fallback: Optional[str] = None) -> str:
    """Name recorded in reviews and the audit log: the signed-in user wins."""
    if user is not None:
        return user.display_name or user.username
    return (fallback or "").strip()[:100] or "anonymous"
