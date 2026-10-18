"""Uygulamanın merkezi loglama yapılandırması."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.paths import LOG_PATH, ensure_application_directories


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging() -> None:
    """Konsol ve dönen dosya loglamasını bir kez yapılandırır."""
    ensure_application_directories()

    root_logger = logging.getLogger()
    if getattr(root_logger, "_servis_atama_configured", False):
        return

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=1_048_576,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger._servis_atama_configured = True  # type: ignore[attr-defined]
