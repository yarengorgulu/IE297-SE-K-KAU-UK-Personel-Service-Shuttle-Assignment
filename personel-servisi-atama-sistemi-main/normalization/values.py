"""Alan değerleri için güvenli ve tahminsiz normalizasyon kuralları."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from normalization.text import comparison_key, is_blank, normalize_text


SUPPORTED_SHIFTS = {
    "08:00-16:00",
    "16:00-00:00",
    "00:00-08:00",
    "08:00-17:30",
}
SUPPORTED_COLLARS = {"MAVİ", "BEYAZ"}
SUPPORTED_STATUSES = {"KADROLU", "STAJYER"}

SHIFT_PATTERN = re.compile(
    r"^(\d{1,2})\s*:\s*(\d{2})\s*(?:[-–—]|/|\\|\s+)\s*"
    r"(\d{1,2})\s*:\s*(\d{2})$"
)


class ValueNormalizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _required_text(value: Any, field_label: str) -> str:
    normalized = normalize_text(value)
    if normalized is None:
        raise ValueNormalizationError("REQUIRED", f"{field_label} boş olamaz.")
    if normalized.startswith("#"):
        raise ValueNormalizationError(
            "EXCEL_ERROR_VALUE",
            f"{field_label} Excel hata değeri içeriyor: {normalized}",
        )
    return normalized


def normalize_shift(value: Any) -> str:
    normalized = _required_text(value, "Vardiya")
    match = SHIFT_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueNormalizationError(
            "INVALID_SHIFT",
            f"Tanınmayan vardiya biçimi: {normalized}",
        )

    start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
    if (
        start_hour > 23
        or end_hour > 23
        or start_minute > 59
        or end_minute > 59
    ):
        raise ValueNormalizationError(
            "INVALID_SHIFT",
            f"Geçersiz saat değeri: {normalized}",
        )

    canonical = (
        f"{start_hour:02d}:{start_minute:02d}-"
        f"{end_hour:02d}:{end_minute:02d}"
    )
    if canonical not in SUPPORTED_SHIFTS:
        raise ValueNormalizationError(
            "INVALID_SHIFT",
            f"Desteklenmeyen vardiya: {canonical}",
        )
    return canonical


def normalize_collar(value: Any) -> str:
    normalized = comparison_key(_required_text(value, "Yaka türü"))
    aliases = {"MAVİ YAKA": "MAVİ", "MAVİ": "MAVİ", "BEYAZ YAKA": "BEYAZ", "BEYAZ": "BEYAZ"}
    canonical = aliases.get(normalized or "")
    if canonical not in SUPPORTED_COLLARS:
        raise ValueNormalizationError(
            "INVALID_COLLAR",
            f"Tanınmayan yaka türü: {normalize_text(value)}",
        )
    return canonical


def normalize_status(value: Any) -> str:
    normalized = comparison_key(_required_text(value, "Personel statüsü"))
    if normalized not in SUPPORTED_STATUSES:
        raise ValueNormalizationError(
            "INVALID_STATUS",
            f"Tanınmayan personel statüsü: {normalize_text(value)}",
        )
    return normalized


def normalize_active_status(value: Any) -> bool:
    if is_blank(value):
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
    normalized = comparison_key(value)
    aliases = {
        "1": True,
        "EVET": True,
        "AKTİF": True,
        "AKTIF": True,
        "TRUE": True,
        "0": False,
        "HAYIR": False,
        "PASİF": False,
        "PASIF": False,
        "FALSE": False,
    }
    if normalized not in aliases:
        raise ValueNormalizationError(
            "INVALID_ACTIVE_STATUS",
            f"Tanınmayan aktiflik değeri: {normalize_text(value)}",
        )
    return aliases[normalized]


def normalize_personnel_number(value: Any) -> str:
    if is_blank(value):
        raise ValueNormalizationError("REQUIRED", "Sicil numarası boş olamaz.")
    if isinstance(value, bool):
        raise ValueNormalizationError("INVALID_PERSONNEL_NUMBER", "Sicil numarası geçersiz.")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueNormalizationError(
                "INVALID_PERSONNEL_NUMBER",
                "Ondalıklı sicil numarası kabul edilmez.",
            )
        return str(int(value))
    normalized = _required_text(value, "Sicil numarası")
    decimal_text = re.fullmatch(r"([+-]?\d+)\.0+", normalized)
    return decimal_text.group(1) if decimal_text else normalized


def normalize_service_code(value: Any) -> str:
    normalized = _required_text(value, "Servis kodu")
    return comparison_key(normalized) or normalized


def normalize_positive_integer(value: Any, field_label: str = "Kapasite") -> int:
    if is_blank(value) or isinstance(value, bool):
        raise ValueNormalizationError(
            "INVALID_CAPACITY",
            f"{field_label} pozitif tam sayı olmalıdır.",
        )
    try:
        decimal_value = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueNormalizationError(
            "INVALID_CAPACITY",
            f"{field_label} pozitif tam sayı olmalıdır.",
        ) from None
    if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
        raise ValueNormalizationError(
            "INVALID_CAPACITY",
            f"{field_label} pozitif tam sayı olmalıdır.",
        )
    result = int(decimal_value)
    if result <= 0:
        raise ValueNormalizationError(
            "INVALID_CAPACITY",
            f"{field_label} sıfırdan büyük olmalıdır.",
        )
    return result


def normalize_coordinate(value: Any, *, latitude: bool) -> float | None:
    if is_blank(value):
        return None
    if isinstance(value, bool):
        raise ValueNormalizationError("INVALID_COORDINATE", "Koordinat sayısal olmalıdır.")
    text_value = str(value).strip()
    if text_value.startswith("#"):
        raise ValueNormalizationError(
            "INVALID_COORDINATE",
            f"Koordinat Excel hata değeri içeriyor: {text_value}",
        )
    if "," in text_value and "." not in text_value:
        text_value = text_value.replace(",", ".")
    try:
        coordinate = float(text_value)
    except (TypeError, ValueError):
        raise ValueNormalizationError(
            "INVALID_COORDINATE",
            "Koordinat sayısal olmalıdır.",
        ) from None
    minimum, maximum = (-90.0, 90.0) if latitude else (-180.0, 180.0)
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        coordinate_name = "Enlem" if latitude else "Boylam"
        raise ValueNormalizationError(
            "INVALID_COORDINATE",
            f"{coordinate_name} {minimum:g} ile {maximum:g} arasında olmalıdır.",
        )
    return coordinate


def normalize_date(value: Any, field_label: str) -> date | None:
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool):
        raise ValueNormalizationError("INVALID_DATE", f"{field_label} geçersiz.")
    if isinstance(value, (int, float)):
        numeric_value = float(value)
        if not math.isfinite(numeric_value) or numeric_value <= 0:
            raise ValueNormalizationError("INVALID_DATE", f"{field_label} geçersiz.")
        try:
            return (datetime(1899, 12, 30) + timedelta(days=numeric_value)).date()
        except (OverflowError, ValueError):
            raise ValueNormalizationError("INVALID_DATE", f"{field_label} geçersiz.") from None

    normalized = _required_text(value, field_label)
    for date_format in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue
    raise ValueNormalizationError(
        "INVALID_DATE",
        f"{field_label} güvenli bir tarih biçimine dönüştürülemedi: {normalized}",
    )
