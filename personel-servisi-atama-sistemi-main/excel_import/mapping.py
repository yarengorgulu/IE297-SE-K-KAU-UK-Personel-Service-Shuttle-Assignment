"""Otomatik ve kullanıcı tarafından değiştirilebilir sütun eşleştirme planı."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from excel_import.models import ColumnMappingEntry, ColumnMappingPlan
from normalization.columns import COLUMN_ALIASES, REQUIRED_COLUMNS, map_column_name
from normalization.result import RecordType
from normalization.text import normalize_header


def build_column_mapping(
    headers: Iterable[Any],
    record_type: RecordType | str,
    overrides: Mapping[int, str | None] | None = None,
) -> ColumnMappingPlan:
    resolved_type = RecordType(record_type)
    manual_overrides = dict(overrides or {})
    allowed_targets = set(COLUMN_ALIASES[resolved_type])
    entries: list[ColumnMappingEntry] = []
    target_sources: dict[str, list[str]] = {}

    for index, header in enumerate(headers):
        source_header = "" if header is None else str(header)
        is_manual = index in manual_overrides
        target = (
            manual_overrides[index]
            if is_manual
            else map_column_name(header, resolved_type)
        )
        if target == "":
            target = None
        if target is not None and target not in allowed_targets:
            raise ValueError(f"Geçersiz hedef sütun: {target}")
        entries.append(
            ColumnMappingEntry(
                source_index=index,
                source_header=source_header,
                normalized_header=normalize_header(header),
                target_field=target,
                is_manual=is_manual,
            )
        )
        if target is not None:
            target_sources.setdefault(target, []).append(source_header)

    duplicate_targets = {
        target: tuple(sources)
        for target, sources in target_sources.items()
        if len(sources) > 1
    }
    missing_required = tuple(
        sorted(REQUIRED_COLUMNS[resolved_type] - set(target_sources))
    )
    return ColumnMappingPlan(
        record_type=resolved_type,
        entries=tuple(entries),
        missing_required_columns=missing_required,
        duplicate_targets=duplicate_targets,
    )
