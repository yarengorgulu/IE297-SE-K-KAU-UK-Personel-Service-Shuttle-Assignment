"""Personel Servisi Atama Sistemi uygulama giriş noktası."""

from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.constants import (
    APPLICATION_DISPLAY_NAME,
    APPLICATION_ICON_PATH,
    ORGANIZATION_NAME,
)
from app.logging_config import configure_logging
from app.paths import DATABASE_PATH, ensure_application_directories
from database.init_db import initialize_database
from ui.main_window import MainWindow


logger = logging.getLogger(__name__)


def _set_windows_app_identity() -> None:
    """Görev çubuğunda uygulama adı ve ikonunun bağımsız görünmesini sağlar."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "SecilKaucuk.PersonelServisiAtamaSistemi"
        )
    except Exception:
        logger.debug("Windows uygulama kimliği ayarlanamadı.", exc_info=True)


def create_application() -> tuple[QApplication, MainWindow]:
    """Qt uygulamasını, veritabanını ve ana pencereyi hazırlar."""
    ensure_application_directories()
    configure_logging()

    logger.info("Uygulama başlatılıyor.")

    initialize_database()

    _set_windows_app_identity()
    application = QApplication(sys.argv)
    application.setApplicationName(APPLICATION_DISPLAY_NAME)
    application.setOrganizationName(ORGANIZATION_NAME)
    if APPLICATION_ICON_PATH.exists():
        application.setWindowIcon(QIcon(str(APPLICATION_ICON_PATH)))

    window = MainWindow(database_path=DATABASE_PATH)
    application.aboutToQuit.connect(
        lambda: logger.info("Uygulama kapatılıyor.")
    )
    return application, window


def main() -> int:
    """Uygulamayı çalıştırır ve işletim sistemine çıkış kodunu döndürür."""
    try:
        application, window = create_application()
        window.show()
        return application.exec()
    except Exception:
        logging.getLogger(__name__).exception(
            "Uygulama başlatılırken beklenmeyen bir hata oluştu."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
