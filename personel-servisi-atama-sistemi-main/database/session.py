"""SQLAlchemy oturum ve transaction yönetimi."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session, sessionmaker

from database.connection import engine


SessionFactory = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] = SessionFactory,
) -> Iterator[Session]:
    """Başarılı işlemde commit, hatada rollback yapan oturum sağlar."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
