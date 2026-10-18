"""Uygulamanın kullanıcıya ait yazılabilir dosya yolları."""

from __future__ import annotations

import os
from pathlib import Path

from app.constants import (
    APPLICATION_DATA_FOLDER_NAME,
    DATABASE_FILE_NAME,
    LOG_FILE_NAME,
)


def _local_app_data_directory() -> Path:
    """Windows Local AppData dizinini güvenli bir geri dönüşle belirler."""
    configured_path = os.getenv("LOCALAPPDATA")
    if configured_path:
        return Path(configured_path)
    return Path.home() / "AppData" / "Local"


APPLICATION_DATA_DIR = _local_app_data_directory() / APPLICATION_DATA_FOLDER_NAME
DATA_DIR = APPLICATION_DATA_DIR / "data"
LOG_DIR = APPLICATION_DATA_DIR / "logs"
DATABASE_PATH = DATA_DIR / DATABASE_FILE_NAME
LOG_PATH = LOG_DIR / LOG_FILE_NAME


def ensure_application_directories() -> None:
    """Veritabanı ve log klasörlerini yoksa oluşturur."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
