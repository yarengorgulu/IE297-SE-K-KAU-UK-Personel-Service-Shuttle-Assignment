"""Kayıt türüne göre satır normalizasyonu ve doğrulaması."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from normalization.columns import map_column_name
from normalization.result import RecordType, RowValidationResult
from normalization.text import (
    comparison_key,
    is_blank,
    is_excel_error,
    normalize_text,
)
from normalization.values import (
    ValueNormalizationError,
    normalize_active_status,
    normalize_collar,
    normalize_coordinate,
    normalize_date,
    normalize_personnel_number,
    normalize_positive_integer,
    normalize_service_code,
    normalize_shift,
    normalize_status,
)


SUSPICIOUS_CITY_VALUES = {"KOACELİ", "KOACELI", "TARSUS"}


def _canonicalize_row(
    row: Mapping[Any, Any],
    record_type: RecordType,
    result: RowValidationResult,
) -> dict[str, Any]:
    canonical_row: dict[str, Any] = {}
    for source_column, value in row.items():
        canonical_name = map_column_name(source_column, record_type)
        if canonical_name is None:
            continue
        if canonical_name in canonical_row:
            result.add_error(
                "DUPLICATE_COLUMN",
                f"Birden fazla sütun {canonical_name} alanına eşlendi.",
                canonical_name,
            )
            continue
        canonical_row[canonical_name] = value
    return canonical_row


def _normalize_with_error(
    result: RowValidationResult,
    field_name: str,
    value: Any,
    normalizer: Callable[[Any], Any],
) -> Any:
    try:
        normalized = normalizer(value)
    except ValueNormalizationError as error:
        result.add_error(error.code, error.message, field_name)
        return None
    result.normalized_data[field_name] = normalized
    return normalized


def _normalize_required_text(
    result: RowValidationResult,
    field_name: str,
    value: Any,
    label: str,
    *,
    preserve_original: bool = False,
) -> str | None:
    if preserve_original:
        result.normalized_data[f"{field_name}_ORIJINAL"] = None if value is None else str(value)
    normalized = normalize_text(value)
    if normalized is None:
        result.add_error("REQUIRED", f"{label} boş olamaz.", field_name)
        return None
    if is_excel_error(normalized):
        result.add_error(
            "EXCEL_ERROR_VALUE",
            f"{label} Excel hata değeri içeriyor: {normalized}",
            field_name,
        )
        return None
    result.normalized_data[field_name] = normalized
    result.normalized_data[f"{field_name}_KARSILASTIRMA"] = comparison_key(normalized)
    return normalized


def _normalize_optional_text(
    result: RowValidationResult,
    field_name: str,
    value: Any,
    label: str,
) -> str | None:
    if is_blank(value):
        result.normalized_data[field_name] = None
        return None
    return _normalize_required_text(result, field_name, value, label)


def _normalize_optional_positive_integer(
    result: RowValidationResult,
    field_name: str,
    value: Any,
) -> int | None:
    if is_blank(value):
        result.normalized_data[field_name] = None
        return None
    return _normalize_with_error(
        result,
        field_name,
        value,
        normalize_positive_integer,
    )


def _normalize_coordinates(
    result: RowValidationResult,
    row: Mapping[str, Any],
) -> None:
    latitude_raw = row.get("ENLEM")
    longitude_raw = row.get("BOYLAM")
    latitude = _normalize_with_error(
        result,
        "ENLEM",
        latitude_raw,
        lambda value: normalize_coordinate(value, latitude=True),
    )
    longitude = _normalize_with_error(
        result,
        "BOYLAM",
        longitude_raw,
        lambda value: normalize_coordinate(value, latitude=False),
    )
    if is_blank(latitude_raw) or is_blank(longitude_raw):
        result.add_warning(
            "MISSING_COORDINATES",
            "Enlem veya boylam eksik; koordinat daha sonra tamamlanmalıdır.",
        )


def validate_personnel_row(
    row_number: int,
    row: Mapping[Any, Any],
) -> RowValidationResult:
    result = RowValidationResult(row_number=row_number, record_type=RecordType.PERSONEL)
    if not row or all(is_blank(value) for value in row.values()):
        result.add_error("EMPTY_ROW", "Satır tamamen boş.")
        return result

    canonical_row = _canonicalize_row(row, RecordType.PERSONEL, result)
    _normalize_with_error(
        result,
        "SICIL_NO",
        canonical_row.get("SICIL_NO"),
        normalize_personnel_number,
    )
    first_name = _normalize_optional_text(
        result,
        "AD",
        canonical_row.get("AD"),
        "Ad",
    )
    if first_name is None:
        result.add_warning(
            "MISSING_FIRST_NAME",
            "Ad bilgisi boş; personel sicil numarasıyla aktarılabilir.",
            "AD",
        )
    last_name = _normalize_optional_text(
        result,
        "SOYAD",
        canonical_row.get("SOYAD"),
        "Soyad",
    )
    if last_name is None:
        result.add_warning(
            "MISSING_LAST_NAME",
            "Soyad bilgisi boş; personel sicil numarasıyla aktarılabilir.",
            "SOYAD",
        )
    _normalize_with_error(
        result,
        "YAKA",
        canonical_row.get("YAKA"),
        normalize_collar,
    )
    _normalize_required_text(
        result,
        "ACIK_ADRES",
        canonical_row.get("ACIK_ADRES"),
        "Açık adres",
        preserve_original=True,
    )
    city = _normalize_required_text(result, "IL", canonical_row.get("IL"), "İl")
    _normalize_required_text(result, "ILCE", canonical_row.get("ILCE"), "İlçe")
    _normalize_required_text(result, "MAHALLE", canonical_row.get("MAHALLE"), "Mahalle")
    _normalize_optional_text(result, "SOKAK", canonical_row.get("SOKAK"), "Sokak")
    _normalize_optional_text(result, "CADDE", canonical_row.get("CADDE"), "Cadde")
    _normalize_coordinates(result, canonical_row)
    _normalize_with_error(
        result,
        "VARDIYA",
        canonical_row.get("VARDIYA"),
        normalize_shift,
    )
    status = _normalize_with_error(
        result,
        "STATU",
        canonical_row.get("STATU"),
        normalize_status,
    )
    _normalize_with_error(
        result,
        "AKTIF_MI",
        canonical_row.get("AKTIF_MI"),
        normalize_active_status,
    )

    start_date = _normalize_with_error(
        result,
        "STAJ_BASLAMA",
        canonical_row.get("STAJ_BASLAMA"),
        lambda value: normalize_date(value, "Staj başlangıç tarihi"),
    )
    end_date = _normalize_with_error(
        result,
        "STAJ_BITIS",
        canonical_row.get("STAJ_BITIS"),
        lambda value: normalize_date(value, "Staj bitiş tarihi"),
    )

    if status == "STAJYER":
        if start_date is None:
            result.add_error(
                "MISSING_INTERNSHIP_DATE",
                "Stajyer için staj başlangıç tarihi zorunludur.",
                "STAJ_BASLAMA",
            )
        if end_date is None:
            result.add_error(
                "MISSING_INTERNSHIP_DATE",
                "Stajyer için staj bitiş tarihi zorunludur.",
                "STAJ_BITIS",
            )
    if start_date is not None and end_date is not None and start_date > end_date:
        result.add_error(
            "INVALID_DATE_RANGE",
            "Staj başlangıç tarihi bitiş tarihinden sonra olamaz.",
            "STAJ_BASLAMA",
        )

    if city is not None and comparison_key(city) in SUSPICIOUS_CITY_VALUES:
        result.add_warning(
            "SUSPICIOUS_CITY",
            f"İl değeri kullanıcı tarafından doğrulanmalıdır: {city}",
            "IL",
        )
    return result


def validate_service_capacity_row(
    row_number: int,
    row: Mapping[Any, Any],
) -> RowValidationResult:
    result = RowValidationResult(
        row_number=row_number,
        record_type=RecordType.SERVIS_KAPASITE,
    )
    if not row or all(is_blank(value) for value in row.values()):
        result.add_error("EMPTY_ROW", "Satır tamamen boş.")
        return result

    canonical_row = _canonicalize_row(row, RecordType.SERVIS_KAPASITE, result)
    _normalize_with_error(
        result,
        "SERVIS_KODU",
        canonical_row.get("SERVIS_KODU"),
        normalize_service_code,
    )
    _normalize_with_error(
        result,
        "KAPASITE",
        canonical_row.get("KAPASITE"),
        normalize_positive_integer,
    )
    _normalize_optional_text(
        result,
        "ROTA_ADI",
        canonical_row.get("ROTA_ADI"),
        "Rota adı",
    )
    _normalize_with_error(
        result,
        "YAKA",
        canonical_row.get("YAKA"),
        normalize_collar,
    )
    _normalize_with_error(
        result,
        "VARDIYA",
        canonical_row.get("VARDIYA"),
        normalize_shift,
    )
    return result


def validate_service_route_row(
    row_number: int,
    row: Mapping[Any, Any],
) -> RowValidationResult:
    result = RowValidationResult(
        row_number=row_number,
        record_type=RecordType.SERVIS_GUZERGAH,
    )
    if not row or all(is_blank(value) for value in row.values()):
        result.add_error("EMPTY_ROW", "Satır tamamen boş.")
        return result

    canonical_row = _canonicalize_row(row, RecordType.SERVIS_GUZERGAH, result)
    _normalize_with_error(
        result,
        "SERVIS_KODU",
        canonical_row.get("SERVIS_KODU"),
        normalize_service_code,
    )
    _normalize_optional_positive_integer(
        result,
        "KAPASITE",
        canonical_row.get("KAPASITE"),
    )
    _normalize_optional_positive_integer(
        result,
        "SIRA_NO",
        canonical_row.get("SIRA_NO"),
    )
    _normalize_optional_text(result, "IL", canonical_row.get("IL"), "İl")
    district = _normalize_optional_text(
        result,
        "ILCE",
        canonical_row.get("ILCE"),
        "İlçe",
    )
    neighborhood = _normalize_optional_text(
        result,
        "MAHALLE",
        canonical_row.get("MAHALLE"),
        "Mahalle",
    )
    _normalize_optional_text(result, "SOKAK", canonical_row.get("SOKAK"), "Sokak")
    _normalize_optional_text(result, "CADDE", canonical_row.get("CADDE"), "Cadde")
    point_name = _normalize_optional_text(
        result,
        "NOKTA_ADI",
        canonical_row.get("NOKTA_ADI"),
        "Nokta adı",
    )
    _normalize_optional_text(
        result,
        "ACIK_ADRES",
        canonical_row.get("ACIK_ADRES"),
        "Açık adres",
    )
    _normalize_optional_text(
        result,
        "ACIKLAMA",
        canonical_row.get("ACIKLAMA"),
        "Açıklama",
    )
    if district is None and neighborhood is None and point_name is None:
        result.add_error(
            "MISSING_ROUTE_POINT",
            "İlçe, mahalle veya nokta adı alanlarından en az biri dolu olmalıdır.",
        )
    if is_blank(canonical_row.get("VARDIYA")):
        result.normalized_data["VARDIYA"] = None
    else:
        _normalize_with_error(
            result,
            "VARDIYA",
            canonical_row.get("VARDIYA"),
            normalize_shift,
        )
    if is_blank(canonical_row.get("YAKA")):
        result.normalized_data["YAKA"] = None
    else:
        _normalize_with_error(
            result,
            "YAKA",
            canonical_row.get("YAKA"),
            normalize_collar,
        )
    _normalize_with_error(
        result,
        "AKTIF_MI",
        canonical_row.get("AKTIF_MI"),
        normalize_active_status,
    )
    latitude = _normalize_with_error(
        result,
        "ENLEM",
        canonical_row.get("ENLEM"),
        lambda value: normalize_coordinate(value, latitude=True),
    )
    longitude = _normalize_with_error(
        result,
        "BOYLAM",
        canonical_row.get("BOYLAM"),
        lambda value: normalize_coordinate(value, latitude=False),
    )
    if (latitude is None) != (longitude is None):
        result.add_error(
            "INCOMPLETE_COORDINATES",
            "Enlem ve boylam birlikte doldurulmalı veya ikisi de boş bırakılmalıdır.",
        )
    return result


def validate_row(
    row_number: int,
    record_type: RecordType | str,
    row: Mapping[Any, Any],
) -> RowValidationResult:
    resolved_type = RecordType(record_type)
    validators = {
        RecordType.PERSONEL: validate_personnel_row,
        RecordType.SERVIS_KAPASITE: validate_service_capacity_row,
        RecordType.SERVIS_GUZERGAH: validate_service_route_row,
    }
    return validators[resolved_type](row_number, row)
