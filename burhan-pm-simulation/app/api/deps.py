from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.db import get_session_factory


def get_db_session():
    session: Session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()

