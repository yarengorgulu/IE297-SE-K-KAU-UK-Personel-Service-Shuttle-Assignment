"""Uygulama genelinde kullanılan sabitler."""

from pathlib import Path

APPLICATION_DISPLAY_NAME = "Seçil Kauçuk Personel Servisi Atama Sistemi"
ORGANIZATION_NAME = "Seçil Kauçuk"
APPLICATION_DATA_FOLDER_NAME = "ServisAtama"
DATABASE_FILE_NAME = "servis_atama.db"
LOG_FILE_NAME = "servis_atama.log"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_LOGO_PATH = PROJECT_ROOT / "assets" / "secil_kaucuk.jpeg"
APPLICATION_ICON_PATH = APPLICATION_LOGO_PATH
