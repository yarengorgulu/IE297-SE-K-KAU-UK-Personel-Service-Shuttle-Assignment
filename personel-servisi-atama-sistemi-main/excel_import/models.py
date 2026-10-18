"""Excel içe aktarma önizlemesinin arayüzden bağımsız veri yapıları."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from normalization.result import RecordType, RowValidationResult


class PreviewStatus(str, Enum):
    GECERLI = "GEÇERLİ"
    UYARILI = "UYARILI"
    HATALI = "HATALI"


@dataclass(frozen=True, slots=True)
class SheetInfo:
    name: str
    is_empty: bool
    header_row_number: int | None = None
    headers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkbookInfo:
    path: Path
    sheets: tuple[SheetInfo, ...]

    @property
    def non_empty_sheets(self) -> tuple[SheetInfo, ...]:
        return tuple(sheet for sheet in self.sheets if not sheet.is_empty)


@dataclass(frozen=True, slots=True)
class ExcelRow:
    row_number: int
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class SheetData:
    name: str
    header_row_number: int
    headers: tuple[str, ...]
    rows: tuple[ExcelRow, ...]
    skipped_blank_rows: int = 0


@dataclass(frozen=True, slots=True)
class FileTypeDetection:
    recommended_type: RecordType | None
    scores: dict[RecordType, float]
    required_match_ratios: dict[RecordType, float]
    is_confident: bool


@dataclass(frozen=True, slots=True)
class ColumnMappingEntry:
    source_index: int
    source_header: str
    normalized_header: str
    target_field: str | None
    is_manual: bool = False


@dataclass(frozen=True, slots=True)
class ColumnMappingPlan:
    record_type: RecordType
    entries: tuple[ColumnMappingEntry, ...]
    missing_required_columns: tuple[str, ...] = ()
    duplicate_targets: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def extra_entries(self) -> tuple[ColumnMappingEntry, ...]:
        return tuple(entry for entry in self.entries if entry.target_field is None)

    @property
    def is_valid(self) -> bool:
        return not self.missing_required_columns and not self.duplicate_targets


@dataclass(slots=True)
class PreviewRow:
    validation: RowValidationResult
    raw_data: dict[str, Any]
    extra_data: dict[str, Any]

    @property
    def row_number(self) -> int:
        return self.validation.row_number

    @property
    def status(self) -> PreviewStatus:
        if self.validation.errors:
            return PreviewStatus.HATALI
        if self.validation.warnings:
            return PreviewStatus.UYARILI
        return PreviewStatus.GECERLI

    @property
    def error_text(self) -> str:
        return "; ".join(message.message for message in self.validation.errors)

    @property
    def warning_text(self) -> str:
        return "; ".join(message.message for message in self.validation.warnings)

    @property
    def preview_data(self) -> dict[str, Any]:
        return {**self.validation.normalized_data, **self.extra_data}


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    workbook_path: Path
    sheet_name: str
    record_type: RecordType
    detection: FileTypeDetection
    mapping: ColumnMappingPlan
    rows: tuple[PreviewRow, ...]
    skipped_blank_rows: int = 0

    @property
    def total_count(self) -> int:
        return len(self.rows)

    @property
    def valid_count(self) -> int:
        return sum(row.status is PreviewStatus.GECERLI for row in self.rows)

    @property
    def warning_count(self) -> int:
        return sum(row.status is PreviewStatus.UYARILI for row in self.rows)

    @property
    def error_count(self) -> int:
        return sum(row.status is PreviewStatus.HATALI for row in self.rows)
