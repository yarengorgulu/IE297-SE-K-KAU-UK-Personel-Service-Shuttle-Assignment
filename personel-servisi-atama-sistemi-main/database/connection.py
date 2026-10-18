"""SQLAlchemy engine ve SQLite bağlantı ayarları."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL

from app.paths import DATABASE_PATH


def create_database_engine(database_path: Path = DATABASE_PATH) -> Engine:
    """Belirtilen SQLite dosyası için yapılandırılmış engine oluşturur."""
    resolved_path = Path(database_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(resolved_path)),
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(
        dbapi_connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.execute("PRAGMA journal_mode = WAL")
        finally:
            cursor.close()

    return engine


engine = create_database_engine()
