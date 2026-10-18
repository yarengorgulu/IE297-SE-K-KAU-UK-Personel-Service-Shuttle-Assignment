"""Normalizasyon ve doğrulama işlemlerinin ortak sonuç yapıları."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecordType(str, Enum):
    PERSONEL = "PERSONEL"
    SERVIS_KAPASITE = "SERVIS_KAPASITE"
    SERVIS_GUZERGAH = "SERVIS_GUZERGAH"


@dataclass(frozen=True, slots=True)
class ValidationMessage:
    code: str
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "field": self.field}


@dataclass(slots=True)
class RowValidationResult:
    row_number: int
    record_type: RecordType
    normalized_data: dict[str, Any] = field(default_factory=dict)
    errors: list[ValidationMessage] = field(default_factory=list)
    warnings: list[ValidationMessage] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add_error(self, code: str, message: str, field_name: str | None = None) -> None:
        self.errors.append(ValidationMessage(code=code, message=message, field=field_name))

    def add_warning(
        self,
        code: str,
        message: str,
        field_name: str | None = None,
    ) -> None:
        self.warnings.append(
            ValidationMessage(code=code, message=message, field=field_name)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "record_type": self.record_type.value,
            "normalized_data": self.normalized_data,
            "errors": [error.to_dict() for error in self.errors],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "is_valid": self.is_valid,
        }
