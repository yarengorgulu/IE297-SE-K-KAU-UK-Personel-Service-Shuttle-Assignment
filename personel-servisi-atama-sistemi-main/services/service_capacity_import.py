"""Doğrulanmış servis kapasite önizlemesini transaction ile veritabanına aktarır."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from database.models import Servis, ServisVardiyasi, VardiyaTuru, YakaTuru
from database.repositories import ServisRepository, ServisVardiyasiRepository
from database.session import SessionFactory, session_scope
from excel_import.models import AnalysisReport, PreviewRow
from normalization.result import RecordType


logger = logging.getLogger(__name__)


class ConflictBehavior(str, Enum):
    SKIP = "SKIP"
    UPDATE = "UPDATE"


class ImportAction(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    SKIP = "SKIP"
    INVALID = "INVALID"


class ServiceCapacityImportError(RuntimeError):
    """Kullanıcıya gösterilebilecek servis kapasite aktarım hatası."""


@dataclass(frozen=True, slots=True)
class ServiceCapacityRecord:
    row_number: int
    service_code: str
    capacity: int
    route_name: str | None
    collar: YakaTuru
    shift: VardiyaTuru
    warnings: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, VardiyaTuru, YakaTuru]:
        return self.service_code, self.shift, self.collar


@dataclass(frozen=True, slots=True)
class ImportPlanEntry:
    row_number: int
    action: ImportAction
    record: ServiceCapacityRecord | None = None
    creates_physical_service: bool = False
    is_conflict: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceCapacityImportPlan:
    conflict_behavior: ConflictBehavior
    entries: tuple[ImportPlanEntry, ...]
    valid_record_count: int
    warning_record_count: int

    @property
    def new_service_count(self) -> int:
        return sum(entry.creates_physical_service for entry in self.entries)

    @property
    def new_shift_count(self) -> int:
        return sum(entry.action is ImportAction.ADD for entry in self.entries)

    @property
    def conflict_count(self) -> int:
        return sum(entry.is_conflict for entry in self.entries)

    @property
    def update_count(self) -> int:
        return sum(entry.action is ImportAction.UPDATE for entry in self.entries)

    @property
    def skip_count(self) -> int:
        return sum(entry.action is ImportAction.SKIP for entry in self.entries)

    @property
    def invalid_count(self) -> int:
        return sum(entry.action is ImportAction.INVALID for entry in self.entries)

    @property
    def invalid_details(self) -> tuple[str, ...]:
        return tuple(
            f"Excel satırı {entry.row_number}: {entry.reason}"
            for entry in self.entries
            if entry.action is ImportAction.INVALID and entry.reason
        )

    @property
    def warning_details(self) -> tuple[str, ...]:
        details: list[str] = []
        for entry in self.entries:
            if entry.record is not None and entry.record.warnings:
                details.append(
                    f"Excel satırı {entry.row_number}: "
                    + "; ".join(entry.record.warnings)
                )
        return tuple(details)


@dataclass(frozen=True, slots=True)
class ServiceCapacityImportResult:
    added_service_count: int
    added_shift_count: int
    updated_count: int
    skipped_count: int
    invalid_count: int
    imported_warning_count: int


class ServiceCapacityImportService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
    ) -> None:
        self.session_factory = session_factory

    def preview_import(
        self,
        report: AnalysisReport,
        conflict_behavior: ConflictBehavior = ConflictBehavior.SKIP,
        *,
        include_warning_rows: bool = False,
    ) -> ServiceCapacityImportPlan:
        self._ensure_capacity_report(report)
        with self.session_factory() as session:
            return self._build_plan(
                session,
                report,
                conflict_behavior,
                include_warning_rows=include_warning_rows,
            )

    def import_report(
        self,
        report: AnalysisReport,
        conflict_behavior: ConflictBehavior = ConflictBehavior.SKIP,
        *,
        include_warning_rows: bool = False,
    ) -> ServiceCapacityImportResult:
        self._ensure_capacity_report(report)
        try:
            with session_scope(self.session_factory) as session:
                plan = self._build_plan(
                    session,
                    report,
                    conflict_behavior,
                    include_warning_rows=include_warning_rows,
                )
                result = self._apply_plan(session, plan)
            logger.info(
                "Servis kapasite aktarımı tamamlandı: servis=%s, vardiya=%s, "
                "güncelleme=%s, atlanan=%s, hatalı=%s",
                result.added_service_count,
                result.added_shift_count,
                result.updated_count,
                result.skipped_count,
                result.invalid_count,
            )
            return result
        except ServiceCapacityImportError:
            raise
        except Exception as error:
            logger.exception("Servis kapasite aktarımı geri alındı.")
            raise ServiceCapacityImportError(
                "Servis kapasite aktarımı tamamlanamadı. Yapılan işlemler geri alındı."
            ) from error

    @staticmethod
    def _ensure_capacity_report(report: AnalysisReport) -> None:
        if report.record_type is not RecordType.SERVIS_KAPASITE:
            raise ServiceCapacityImportError(
                "Yalnızca Servis Kapasite dosyası veritabanına aktarılabilir."
            )

    def _build_plan(
        self,
        session: Session,
        report: AnalysisReport,
        conflict_behavior: ConflictBehavior,
        *,
        include_warning_rows: bool,
    ) -> ServiceCapacityImportPlan:
        behavior = ConflictBehavior(conflict_behavior)
        candidate_codes = {
            str(row.validation.normalized_data.get("SERVIS_KODU", "")).strip()
            for row in report.rows
            if row.validation.is_valid
        }
        candidate_codes.discard("")
        service_repository = ServisRepository(session)
        existing_services = {
            service.servis_kodu: service
            for service in service_repository.get_by_codes(candidate_codes)
        }
        known_service_codes = set(existing_services)
        known_shift_keys = {
            (service.servis_kodu, shift.vardiya_turu, shift.yaka_turu)
            for service in existing_services.values()
            for shift in service.vardiyalar
        }

        entries: list[ImportPlanEntry] = []
        valid_record_count = 0
        warning_record_count = 0
        for row in report.rows:
            if row.validation.errors:
                entries.append(
                    ImportPlanEntry(
                        row_number=row.row_number,
                        action=ImportAction.INVALID,
                        reason=row.error_text or "Satır doğrulama hatası içeriyor.",
                    )
                )
                continue
            try:
                record = self._record_from_preview(row)
            except (TypeError, ValueError) as error:
                entries.append(
                    ImportPlanEntry(
                        row_number=row.row_number,
                        action=ImportAction.INVALID,
                        reason=str(error),
                    )
                )
                continue

            valid_record_count += 1
            if record.warnings:
                warning_record_count += 1
                if not include_warning_rows:
                    entries.append(
                        ImportPlanEntry(
                            row_number=row.row_number,
                            action=ImportAction.SKIP,
                            record=record,
                            reason="Uyarılı kayıt için kullanıcı onayı verilmedi.",
                        )
                    )
                    continue

            is_new_service = record.service_code not in known_service_codes
            if is_new_service:
                known_service_codes.add(record.service_code)

            if record.identity in known_shift_keys:
                entries.append(
                    ImportPlanEntry(
                        row_number=row.row_number,
                        action=(
                            ImportAction.UPDATE
                            if behavior is ConflictBehavior.UPDATE
                            else ImportAction.SKIP
                        ),
                        record=record,
                        is_conflict=True,
                        reason=(
                            "Mevcut servis vardiyası güncellenecek."
                            if behavior is ConflictBehavior.UPDATE
                            else "Mevcut servis vardiyası korunarak satır atlanacak."
                        ),
                    )
                )
                continue

            known_shift_keys.add(record.identity)
            entries.append(
                ImportPlanEntry(
                    row_number=row.row_number,
                    action=ImportAction.ADD,
                    record=record,
                    creates_physical_service=is_new_service,
                )
            )

        return ServiceCapacityImportPlan(
            conflict_behavior=behavior,
            entries=tuple(entries),
            valid_record_count=valid_record_count,
            warning_record_count=warning_record_count,
        )

    @staticmethod
    def _record_from_preview(row: PreviewRow) -> ServiceCapacityRecord:
        data: dict[str, Any] = row.validation.normalized_data
        service_code = str(data.get("SERVIS_KODU") or "").strip()
        if not service_code:
            raise ValueError("Servis kodu boş olamaz.")
        capacity = data.get("KAPASITE")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("Kapasite pozitif tam sayı olmalıdır.")
        try:
            shift = VardiyaTuru(data.get("VARDIYA"))
        except (TypeError, ValueError):
            raise ValueError("Vardiya değeri geçersiz veya boş.") from None
        try:
            collar = YakaTuru(data.get("YAKA"))
        except (TypeError, ValueError):
            raise ValueError("Yaka türü yalnızca MAVİ veya BEYAZ olabilir.") from None
        route_value = data.get("ROTA_ADI")
        route_name = None if route_value is None else str(route_value).strip() or None
        return ServiceCapacityRecord(
            row_number=row.row_number,
            service_code=service_code,
            capacity=capacity,
            route_name=route_name,
            collar=collar,
            shift=shift,
            warnings=tuple(message.message for message in row.validation.warnings),
        )

    def _apply_plan(
        self,
        session: Session,
        plan: ServiceCapacityImportPlan,
    ) -> ServiceCapacityImportResult:
        records = [entry.record for entry in plan.entries if entry.record is not None]
        service_codes = {record.service_code for record in records}
        service_repository = ServisRepository(session)
        shift_repository = ServisVardiyasiRepository(session)
        service_cache = {
            service.servis_kodu: service
            for service in service_repository.get_by_codes(service_codes)
        }
        shift_cache = {
            (service.servis_kodu, shift.vardiya_turu, shift.yaka_turu): shift
            for service in service_cache.values()
            for shift in service.vardiyalar
        }

        added_services = 0
        added_shifts = 0
        updated = 0
        imported_warnings = 0
        for entry in plan.entries:
            if entry.action not in {ImportAction.ADD, ImportAction.UPDATE}:
                continue
            if entry.record is None:
                raise RuntimeError("Aktarım planında kayıt verisi bulunamadı.")
            added_service, added_shift, updated_shift = self._apply_entry(
                entry,
                service_repository,
                shift_repository,
                service_cache,
                shift_cache,
            )
            added_services += added_service
            added_shifts += added_shift
            updated += updated_shift
            if entry.record.warnings:
                imported_warnings += 1

        return ServiceCapacityImportResult(
            added_service_count=added_services,
            added_shift_count=added_shifts,
            updated_count=updated,
            skipped_count=plan.skip_count,
            invalid_count=plan.invalid_count,
            imported_warning_count=imported_warnings,
        )

    def _apply_entry(
        self,
        entry: ImportPlanEntry,
        service_repository: ServisRepository,
        shift_repository: ServisVardiyasiRepository,
        service_cache: dict[str, Servis],
        shift_cache: dict[
            tuple[str, VardiyaTuru, YakaTuru],
            ServisVardiyasi,
        ],
    ) -> tuple[int, int, int]:
        record = entry.record
        if record is None:
            raise RuntimeError("Aktarım satırında servis kapasite verisi bulunamadı.")
        service = service_cache.get(record.service_code)
        added_service = 0
        if service is None:
            service = service_repository.add(Servis(servis_kodu=record.service_code))
            service_cache[record.service_code] = service
            added_service = 1

        identity = record.identity
        if entry.action is ImportAction.ADD:
            shift = ServisVardiyasi(
                servis_id=service.id,
                vardiya_turu=record.shift,
                yaka_turu=record.collar,
                kapasite=record.capacity,
                rota_adi=record.route_name,
                aktif_mi=True,
            )
            shift_repository.add(shift)
            shift_cache[identity] = shift
            return added_service, 1, 0

        shift = shift_cache.get(identity)
        if shift is None:
            shift = shift_repository.get_by_identity(
                service.id,
                record.shift,
                record.collar,
            )
        if shift is None:
            raise RuntimeError("Güncellenecek servis vardiyası bulunamadı.")
        shift.kapasite = record.capacity
        shift.rota_adi = record.route_name
        shift.aktif_mi = True
        session = shift_repository.session
        session.flush()
        shift_cache[identity] = shift
        return added_service, 0, 1
