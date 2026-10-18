"""Sütun adlarından Excel kayıt türünü tahmin eder."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from excel_import.models import FileTypeDetection
from normalization.columns import REQUIRED_COLUMNS, map_column_name
from normalization.result import RecordType


def detect_record_type(headers: Iterable[Any]) -> FileTypeDetection:
    header_list = list(headers)
    scores: dict[RecordType, float] = {}
    required_ratios: dict[RecordType, float] = {}

    for record_type in RecordType:
        matched = {
            target
            for header in header_list
            if (target := map_column_name(header, record_type)) is not None
        }
        required = REQUIRED_COLUMNS[record_type]
        required_ratio = len(matched & required) / len(required)
        scores[record_type] = required_ratio
        required_ratios[record_type] = required_ratio

    ranked = sorted(scores, key=scores.get, reverse=True)
    best = ranked[0]
    second_score = scores[ranked[1]]
    is_confident = (
        scores[best] > 0
        and required_ratios[best] == 1.0
        and scores[best] - second_score >= 0.15
    )
    return FileTypeDetection(
        recommended_type=best if scores[best] > 0 else None,
        scores=scores,
        required_match_ratios=required_ratios,
        is_confident=is_confident,
    )
