"""Excel okuma, tür algılama ve önizleme raporu altyapısı."""

from excel_import.analyzer import (
    FileTypeSelectionRequired,
    analyze_workbook,
)
from excel_import.detector import detect_record_type
from excel_import.mapping import build_column_mapping
from excel_import.models import (
    AnalysisReport,
    ColumnMappingPlan,
    FileTypeDetection,
    PreviewRow,
    PreviewStatus,
    SheetData,
    SheetInfo,
    WorkbookInfo,
)
from excel_import.reader import ExcelReadError, inspect_workbook, read_sheet

__all__ = [
    "AnalysisReport",
    "ColumnMappingPlan",
    "ExcelReadError",
    "FileTypeDetection",
    "FileTypeSelectionRequired",
    "PreviewRow",
    "PreviewStatus",
    "SheetData",
    "SheetInfo",
    "WorkbookInfo",
    "analyze_workbook",
    "build_column_mapping",
    "detect_record_type",
    "inspect_workbook",
    "read_sheet",
]
