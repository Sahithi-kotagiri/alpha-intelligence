"""Authentication and user isolation.

AUTH_MODE=local: single-user prototype; every request is the local user (id 1).
AUTH_MODE=token: users register/log in; passwords hashed with scrypt (stdlib);
session tokens are random 256-bit values stored only as SHA-256 hashes, with expiry.
Every user-owned query filters by the authenticated user's id.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.database.models import AuthSession, User
from backend.database.session import get_db

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
LOCAL_USER_ID = 1


def valid_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if not TICKER_RE.match(t):
        raise HTTPException(422, "Invalid ticker format")
    return t


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, dk = stored.split("$")
    test = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(test.hex(), dk)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User, settings: Settings) -> str:
    tok = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=token_hash(tok),
                       expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)))
    db.commit()
    return tok


def ensure_local_user(db: Session) -> User:
    u = db.get(User, LOCAL_USER_ID)
    if u is None:
        u = User(id=LOCAL_USER_ID, email="local@localhost", display_name="Local user")
        db.add(u)
        db.commit()
    return u


def current_user(db: Annotated[Session, Depends(get_db)], settings: Annotated[Settings, Depends(get_settings)],
                 authorization: Annotated[str | None, Header()] = None) -> User:
    if settings.auth_mode == "local":
        return ensure_local_user(db)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    tok = authorization.split(" ", 1)[1].strip()
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(tok)))
    if sess is None:
        raise HTTPException(401, "Invalid or expired session")
    exp = sess.expires_at if sess.expires_at.tzinfo else sess.expires_at.replace(tzinfo=UTC)
    if exp < datetime.now(UTC):
        raise HTTPException(401, "Invalid or expired session")
    return db.get(User, sess.user_id)


CurrentUser = Annotated[User, Depends(current_user)]
DB = Annotated[Session, Depends(get_db)]
