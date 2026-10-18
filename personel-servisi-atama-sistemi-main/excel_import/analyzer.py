"""Excel satırlarını 3. aşama doğrulayıcılarıyla önizleme raporuna dönüştürür."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from excel_import.detector import detect_record_type
from excel_import.mapping import build_column_mapping
from excel_import.models import AnalysisReport, PreviewRow
from excel_import.reader import read_sheet
from normalization.result import RecordType
from normalization.validators import validate_row


class FileTypeSelectionRequired(RuntimeError):
    """Otomatik tür tahmini kullanıcı onayı gerektirdiğinde üretilir."""


def _unique_key(existing: dict[str, object], header: str, index: int) -> str:
    base = header or f"SÜTUN_{index + 1}"
    if base not in existing:
        return base
    counter = 2
    while f"{base} ({counter})" in existing:
        counter += 1
    return f"{base} ({counter})"


def analyze_workbook(
    path: str | Path,
    sheet_name: str,
    record_type: RecordType | str | None = None,
    mapping_overrides: Mapping[int, str | None] | None = None,
) -> AnalysisReport:
    sheet = read_sheet(path, sheet_name)
    detection = detect_record_type(sheet.headers)
    if record_type is None:
        if not detection.is_confident or detection.recommended_type is None:
            raise FileTypeSelectionRequired(
                "Dosya türü kesin olarak belirlenemedi. Lütfen dosya türünü seçin."
            )
        resolved_type = detection.recommended_type
    else:
        resolved_type = RecordType(record_type)

    mapping = build_column_mapping(
        sheet.headers,
        resolved_type,
        mapping_overrides,
    )
    preview_rows: list[PreviewRow] = []

    for excel_row in sheet.rows:
        raw_data: dict[str, object] = {}
        canonical_data: dict[str, object] = {}
        extra_data: dict[str, object] = {}

        for entry in mapping.entries:
            value = (
                excel_row.values[entry.source_index]
                if entry.source_index < len(excel_row.values)
                else None
            )
            display_key = _unique_key(raw_data, entry.source_header, entry.source_index)
            raw_data[display_key] = value
            if entry.target_field is None:
                extra_data[display_key] = value
            elif entry.target_field not in canonical_data:
                canonical_data[entry.target_field] = value

        validation = validate_row(
            excel_row.row_number,
            resolved_type,
            canonical_data,
        )
        preview_rows.append(
            PreviewRow(
                validation=validation,
                raw_data=raw_data,
                extra_data=extra_data,
            )
        )

    return AnalysisReport(
        workbook_path=Path(path),
        sheet_name=sheet_name,
        record_type=resolved_type,
        detection=detection,
        mapping=mapping,
        rows=tuple(preview_rows),
        skipped_blank_rows=sheet.skipped_blank_rows,
    )
