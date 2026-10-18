"""Türkçe karakterleri koruyan ortak metin temizleme yardımcıları."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


WHITESPACE_PATTERN = re.compile(r"\s+", flags=re.UNICODE)
TURKISH_UPPER_TRANSLATION = str.maketrans({"i": "İ", "ı": "I"})


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def normalize_whitespace(value: Any) -> str:
    """Satır sonlarını ve tekrarlı boşlukları tek boşluğa indirir."""
    text = unicodedata.normalize("NFC", str(value))
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def normalize_text(value: Any) -> str | None:
    if is_blank(value):
        return None
    normalized = normalize_whitespace(value)
    return normalized or None


def turkish_upper(value: str) -> str:
    """Türkçe i/ı karakterlerini koruyarak büyük harfe dönüştürür."""
    return value.translate(TURKISH_UPPER_TRANSLATION).upper()


def comparison_key(value: Any) -> str | None:
    normalized = normalize_text(value)
    if normalized is None:
        return None
    return turkish_upper(normalized)


def normalize_header(value: Any) -> str:
    """Sütun adını eşleştirmeye uygun, Türkçe karakterli biçime getirir."""
    return comparison_key(value) or ""


def is_excel_error(value: Any) -> bool:
    normalized = normalize_text(value)
    return bool(normalized and normalized.startswith("#"))
