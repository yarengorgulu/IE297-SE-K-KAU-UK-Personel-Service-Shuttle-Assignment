"""Excel sütun adlarının veri türüne göre ortak alanlara eşlenmesi."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from normalization.result import RecordType
from normalization.text import normalize_header


COLUMN_ALIASES: dict[RecordType, dict[str, set[str]]] = {
    RecordType.PERSONEL: {
        "SICIL_NO": {"SICIL_NO", "SİCİL_NO", "YENİ SİCİL NO", "YENI SİCİL NO"},
        "AD": {"AD", "ADI", "İSİM", "ISIM"},
        "SOYAD": {"SOYAD", "SOYADI"},
        "YAKA": {
            "YAKA",
            "MAVİ YAKA BEYAZ YAKA",
            "MAVİ YAKA / BEYAZ YAKA",
            "MAVİ YAKA",
            "BEYAZ YAKA",
        },
        "ACIK_ADRES": {"ACIK_ADRES", "AÇIK_ADRES", "ADRES (OTURDUĞU)"},
        "IL": {"IL", "İL"},
        "ILCE": {"ILCE", "İLÇE"},
        "MAHALLE": {"MAHALLE"},
        "SOKAK": {"SOKAK", "SOKAK ADI", "SOKAK_ADI"},
        "CADDE": {"CADDE", "CADDE ADI", "CADDE_ADI", "BULVAR"},
        "ENLEM": {"ENLEM"},
        "BOYLAM": {"BOYLAM"},
        "VARDIYA": {"VARDIYA", "VARDİYA"},
        "STATU": {"STATU", "STATÜ"},
        "STAJ_BASLAMA": {
            "STAJ_BASLAMA",
            "STAJ BAŞLAMA TARİHİ",
            "STAJ BASLAMA TARIHI",
        },
        "STAJ_BITIS": {
            "STAJ_BITIS",
            "STAJ BİTİŞ TARİHİ",
            "STAJ BITIS TARIHI",
        },
        "AKTIF_MI": {
            "AKTIF_MI",
            "AKTİF_Mİ",
            "AKTIF MI",
            "AKTİF Mİ",
            "AKTIF",
            "AKTİF",
        },
    },
    RecordType.SERVIS_KAPASITE: {
        "SERVIS_KODU": {"SERVIS_KODU", "SERVİS KODU", "SERVIS KODU"},
        "KAPASITE": {
            "KAPASITE",
            "KAPASİTE",
            "SERVİS KAPASİTELERİ",
            "SERVIS KAPASITELERI",
        },
        "ROTA_ADI": {"ROTA_ADI", "ROTA ADI", "SERVİS ROTASI", "SERVIS ROTASI"},
        "YAKA": {"YAKA", "YAKA TÜRÜ", "YAKA TURU"},
        "VARDIYA": {"VARDIYA", "VARDİYA TÜRÜ", "VARDIYA TURU"},
    },
    RecordType.SERVIS_GUZERGAH: {
        "SERVIS_KODU": {"SERVIS_KODU", "SERVİS KODU", "SERVIS KODU"},
        "KAPASITE": {"KAPASITE", "KAPASİTE", "ARAÇ KAPASİTESİ", "ARAC KAPASITESI"},
        "SIRA_NO": {"SIRA_NO", "SIRA NO", "SIRA", "SIRA NUMARASI"},
        "IL": {"IL", "İL"},
        "MAHALLE": {"MAHALLE"},
        "SOKAK": {"SOKAK", "SOKAK ADI", "SOKAK_ADI"},
        "CADDE": {"CADDE", "CADDE ADI", "CADDE_ADI", "BULVAR"},
        "ILCE": {"ILCE", "İLÇE"},
        "NOKTA_ADI": {"NOKTA_ADI", "NOKTA ADI", "DURAK", "DURAK ADI"},
        "ACIK_ADRES": {"ACIK_ADRES", "AÇIK ADRES", "ADRES"},
        "ACIKLAMA": {"ACIKLAMA", "AÇIKLAMA"},
        "ENLEM": {"ENLEM"},
        "BOYLAM": {"BOYLAM"},
        "VARDIYA": {"VARDIYA", "VARDİYA", "VARDİYA TÜRÜ", "VARDIYA TURU"},
        "YAKA": {"YAKA", "YAKA TÜRÜ", "YAKA TURU"},
        "AKTIF_MI": {"AKTIF_MI", "AKTİF Mİ", "AKTIF", "AKTİF"},
    },
}


REQUIRED_COLUMNS: dict[RecordType, set[str]] = {
    RecordType.PERSONEL: {
        "SICIL_NO",
        "YAKA",
        "ACIK_ADRES",
        "IL",
        "ILCE",
        "MAHALLE",
        "VARDIYA",
        "STATU",
    },
    RecordType.SERVIS_KAPASITE: {
        "SERVIS_KODU",
        "KAPASITE",
        "YAKA",
        "VARDIYA",
    },
    RecordType.SERVIS_GUZERGAH: {"SERVIS_KODU", "MAHALLE", "ILCE"},
}


def _build_lookup(record_type: RecordType) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical_name, aliases in COLUMN_ALIASES[record_type].items():
        for alias in aliases:
            normalized_alias = normalize_header(alias)
            existing = lookup.get(normalized_alias)
            if existing is not None and existing != canonical_name:
                raise ValueError(f"Çakışan sütun eş anlamlısı: {alias}")
            lookup[normalized_alias] = canonical_name
    return lookup


COLUMN_LOOKUPS = {
    record_type: _build_lookup(record_type) for record_type in RecordType
}


@dataclass(slots=True)
class ColumnMappingResult:
    record_type: RecordType
    mapped_columns: dict[str, str] = field(default_factory=dict)
    mapped_indexes: dict[int, str] = field(default_factory=dict)
    normalized_headers: list[str] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    duplicate_targets: dict[str, list[str]] = field(default_factory=dict)
    missing_required_columns: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.duplicate_targets and not self.missing_required_columns


def map_column_name(column_name: Any, record_type: RecordType | str) -> str | None:
    resolved_type = RecordType(record_type)
    return COLUMN_LOOKUPS[resolved_type].get(normalize_header(column_name))


def map_columns(
    column_names: Iterable[Any],
    record_type: RecordType | str,
) -> ColumnMappingResult:
    resolved_type = RecordType(record_type)
    result = ColumnMappingResult(record_type=resolved_type)
    target_sources: dict[str, list[str]] = {}

    for index, column_name in enumerate(column_names):
        source_name = "" if column_name is None else str(column_name)
        normalized_name = normalize_header(column_name)
        result.normalized_headers.append(normalized_name)
        canonical_name = map_column_name(column_name, resolved_type)
        if canonical_name is None:
            result.unmapped_columns.append(source_name)
            continue
        result.mapped_columns[source_name] = canonical_name
        result.mapped_indexes[index] = canonical_name
        target_sources.setdefault(canonical_name, []).append(source_name)

    result.duplicate_targets = {
        target: sources for target, sources in target_sources.items() if len(sources) > 1
    }
    result.missing_required_columns = sorted(
        REQUIRED_COLUMNS[resolved_type] - set(target_sources)
    )
    return result
