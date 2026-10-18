"""Veritabanında standartlaştırılmış kategorik değerler."""

from __future__ import annotations

from enum import Enum

from sqlalchemy import Enum as SqlEnum


class YakaTuru(str, Enum):
    MAVI = "MAVİ"
    BEYAZ = "BEYAZ"


class VardiyaTuru(str, Enum):
    SABAH = "08:00-16:00"
    AKSAM = "16:00-00:00"
    GECE = "00:00-08:00"
    IDARI = "08:00-17:30"


class PersonelTuru(str, Enum):
    KADROLU = "KADROLU"
    STAJYER = "STAJYER"


class AtamaDurumu(str, Enum):
    ATANDI = "ATANDI"
    ATANAMADI = "ATANAMADI"
    MANUEL = "MANUEL"


def enum_type(enum_class: type[Enum], constraint_name: str) -> SqlEnum:
    """Enum değerlerini SQLite CHECK kısıtıyla metin olarak saklar."""
    return SqlEnum(
        enum_class,
        values_callable=lambda members: [member.value for member in members],
        name=constraint_name,
        native_enum=False,
        validate_strings=True,
        create_constraint=True,
    )
