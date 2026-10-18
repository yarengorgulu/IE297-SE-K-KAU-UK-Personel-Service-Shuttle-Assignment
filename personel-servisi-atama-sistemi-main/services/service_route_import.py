"""Doğrulanmış servis güzergâhı Excel satırlarını kontrollü olarak içe aktarır."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from database.models import (
    Servis,
    ServisGuzergahNoktasi,
    ServisVardiyasi,
    VardiyaTuru,
    YakaTuru,
)
from database.repositories import (
    RouteGeometryRepository,
    RoutePointRepository,
    ServisRepository,
)
from database.session import SessionFactory, session_scope
from excel_import.models import AnalysisReport, PreviewRow
from normalization.address import extract_address_roads
from normalization.result import RecordType
from normalization.text import comparison_key
from services.geocoding import AddressInput
from services.service_capacity_import import ImportAction


logger = logging.getLogger(__name__)


class ServiceRouteImportError(RuntimeError):
    """Kullanıcıya gösterilebilecek güzergâh Excel aktarım hatası."""


@dataclass(frozen=True, slots=True)
class ServiceRouteImportRecord:
    row_number: int
    service_code: str
    shift: VardiyaTuru | None
    collar: YakaTuru | None
    sequence: int | None
    city: str | None
    district: str | None
    neighborhood: str | None
    street: str | None
    avenue: str | None
    point_name: str | None
    address: str | None
    description: str | None
    latitude: float | None
    longitude: float | None
    active: bool
    warnings: tuple[str, ...] = ()

    @property
    def point_key(self) -> tuple[str, ...]:
        return tuple(
            comparison_key(value) or ""
            for value in (
                self.city,
                self.district,
                self.neighborhood,
                self.point_name,
            )
        )


@dataclass(frozen=True, slots=True)
class ServiceRouteImportPlanEntry:
    row_number: int
    action: ImportAction
    record: ServiceRouteImportRecord | None = None
    service_shift_id: int | None = None
    assigned_sequence: int | None = None
    reason: str | None = None
    is_duplicate: bool = False
    is_ambiguous: bool = False
    service_missing: bool = False


@dataclass(frozen=True, slots=True)
class ServiceRouteImportPlan:
    entries: tuple[ServiceRouteImportPlanEntry, ...]

    @property
    def add_count(self) -> int:
        return sum(entry.action is ImportAction.ADD for entry in self.entries)

    @property
    def skip_count(self) -> int:
        return sum(entry.action is ImportAction.SKIP for entry in self.entries)

    @property
    def invalid_count(self) -> int:
        return sum(entry.action is ImportAction.INVALID for entry in self.entries)

    @property
    def duplicate_count(self) -> int:
        return sum(entry.is_duplicate for entry in self.entries)

    @property
    def ambiguous_count(self) -> int:
        return sum(entry.is_ambiguous for entry in self.entries)

    @property
    def missing_service_count(self) -> int:
        return sum(entry.service_missing for entry in self.entries)

    @property
    def warning_details(self) -> tuple[str, ...]:
        return tuple(
            f"Excel satırı {entry.row_number}: {entry.reason}"
            for entry in self.entries
            if entry.reason
        )


@dataclass(frozen=True, slots=True)
class ServiceRouteImportResult:
    added_count: int
    skipped_count: int
    invalid_count: int
    duplicate_count: int
    ambiguous_count: int
    missing_service_count: int


class ServiceRouteImportService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
    ) -> None:
        self.session_factory = session_factory

    def preview_import(self, report: AnalysisReport) -> ServiceRouteImportPlan:
        self._ensure_route_report(report)
        with self.session_factory() as session:
            return self._build_plan(session, report)

    def import_report(self, report: AnalysisReport) -> ServiceRouteImportResult:
        self._ensure_route_report(report)
        try:
            with session_scope(self.session_factory) as session:
                plan = self._build_plan(session, report)
                result = self._apply_plan(session, plan)
            logger.info(
                "Güzergâh Excel aktarımı tamamlandı: eklenen=%s, atlanan=%s, "
                "hatalı=%s, belirsiz=%s",
                result.added_count,
                result.skipped_count,
                result.invalid_count,
                result.ambiguous_count,
            )
            return result
        except ServiceRouteImportError:
            raise
        except Exception as error:
            logger.exception("Güzergâh Excel aktarımı geri alındı.")
            raise ServiceRouteImportError(
                "Güzergâh aktarımı tamamlanamadı. Yapılan işlemler geri alındı."
            ) from error

    @staticmethod
    def _ensure_route_report(report: AnalysisReport) -> None:
        if report.record_type is not RecordType.SERVIS_GUZERGAH:
            raise ServiceRouteImportError(
                "Yalnızca Servis Mahalle/Güzergâh dosyası aktarılabilir."
            )

    def _build_plan(
        self,
        session: Session,
        report: AnalysisReport,
    ) -> ServiceRouteImportPlan:
        candidate_codes = {
            str(row.validation.normalized_data.get("SERVIS_KODU") or "").strip()
            for row in report.rows
            if row.validation.is_valid
        }
        candidate_codes.discard("")
        services = {
            service.servis_kodu: service
            for service in ServisRepository(session).get_by_codes(candidate_codes)
        }
        route_repository = RoutePointRepository(session)
        known_keys: dict[int, set[tuple[str, ...]]] = {}
        used_sequences: dict[int, set[int]] = {}
        next_sequences: dict[int, int] = {}
        entries: list[ServiceRouteImportPlanEntry] = []

        for row in report.rows:
            if row.validation.errors:
                entries.append(
                    ServiceRouteImportPlanEntry(
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
                    ServiceRouteImportPlanEntry(
                        row_number=row.row_number,
                        action=ImportAction.INVALID,
                        reason=str(error),
                    )
                )
                continue

            service = services.get(record.service_code)
            if service is None:
                entries.append(
                    ServiceRouteImportPlanEntry(
                        row_number=row.row_number,
                        action=ImportAction.SKIP,
                        record=record,
                        reason=(
                            f"{record.service_code} kodlu fiziksel servis veritabanında "
                            "bulunamadı; otomatik servis oluşturulmadı."
                        ),
                        service_missing=True,
                    )
                )
                continue

            target_shifts, reason = self._resolve_shifts(service, record)
            if not target_shifts:
                entries.append(
                    ServiceRouteImportPlanEntry(
                        row_number=row.row_number,
                        action=ImportAction.SKIP,
                        record=record,
                        reason=reason,
                    )
                )
                continue

            for target_shift in target_shifts:
                shift_id = target_shift.id
                if shift_id not in known_keys:
                    existing_points = route_repository.list_for_shift(shift_id)
                    known_keys[shift_id] = {
                        self._point_key(
                            point.il,
                            point.ilce,
                            point.mahalle,
                            point.nokta_adi,
                        )
                        for point in existing_points
                    }
                    used_sequences[shift_id] = {
                        point.sira_no for point in existing_points
                    }
                    next_sequences[shift_id] = (
                        route_repository.max_sequence(shift_id) + 1
                    )

                if record.point_key in known_keys[shift_id]:
                    entries.append(
                        ServiceRouteImportPlanEntry(
                            row_number=row.row_number,
                            action=ImportAction.SKIP,
                            record=record,
                            service_shift_id=shift_id,
                            reason="Aynı güzergâh noktası bu servis-vardiyada kayıtlı.",
                            is_duplicate=True,
                        )
                    )
                    continue

                assigned_sequence = record.sequence
                if assigned_sequence is not None:
                    if assigned_sequence in used_sequences[shift_id]:
                        entries.append(
                            ServiceRouteImportPlanEntry(
                                row_number=row.row_number,
                                action=ImportAction.INVALID,
                                record=record,
                                service_shift_id=shift_id,
                                reason=(
                                    f"{assigned_sequence} sıra numarası bu "
                                    "servis-vardiyada zaten kullanılıyor."
                                ),
                            )
                        )
                        continue
                else:
                    assigned_sequence = next_sequences[shift_id]
                    while assigned_sequence in used_sequences[shift_id]:
                        assigned_sequence += 1
                    next_sequences[shift_id] = assigned_sequence + 1

                known_keys[shift_id].add(record.point_key)
                used_sequences[shift_id].add(assigned_sequence)
                entries.append(
                    ServiceRouteImportPlanEntry(
                        row_number=row.row_number,
                        action=ImportAction.ADD,
                        record=record,
                        service_shift_id=shift_id,
                        assigned_sequence=assigned_sequence,
                    )
                )

        return ServiceRouteImportPlan(entries=tuple(entries))

    @staticmethod
    def _resolve_shifts(
        service: Servis,
        record: ServiceRouteImportRecord,
    ) -> tuple[list[ServisVardiyasi], str | None]:
        candidates = list(service.vardiyalar)
        if record.shift is not None:
            candidates = [
                item for item in candidates if item.vardiya_turu is record.shift
            ]
        if record.collar is not None:
            candidates = [
                item for item in candidates if item.yaka_turu is record.collar
            ]
        if candidates:
            return candidates, None
        return (
            [],
            f"{record.service_code} için Excel satırındaki vardiya/yaka ile "
            "eşleşen servis-vardiya bulunamadı.",
        )

    @staticmethod
    def _record_from_preview(row: PreviewRow) -> ServiceRouteImportRecord:
        data: dict[str, Any] = row.validation.normalized_data
        service_code = str(data.get("SERVIS_KODU") or "").strip()
        if not service_code:
            raise ValueError("Servis kodu boş olamaz.")
        shift_value = data.get("VARDIYA")
        collar_value = data.get("YAKA")
        shift = None if shift_value is None else VardiyaTuru(shift_value)
        collar = None if collar_value is None else YakaTuru(collar_value)
        sequence = data.get("SIRA_NO")
        if sequence is not None and (
            isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0
        ):
            raise ValueError("Sıra numarası pozitif tam sayı olmalıdır.")
        active = data.get("AKTIF_MI", True)
        if not isinstance(active, bool):
            raise ValueError("Aktiflik değeri geçersiz.")
        latitude = data.get("ENLEM")
        longitude = data.get("BOYLAM")
        if (latitude is None) != (longitude is None):
            raise ValueError(
                "Enlem ve boylam birlikte doldurulmalı veya ikisi de boş bırakılmalıdır."
            )
        roads = extract_address_roads(data.get("ACIK_ADRES"))
        return ServiceRouteImportRecord(
            row_number=row.row_number,
            service_code=service_code,
            shift=shift,
            collar=collar,
            sequence=sequence,
            city=data.get("IL"),
            district=data.get("ILCE"),
            neighborhood=data.get("MAHALLE"),
            street=data.get("SOKAK") or roads.street,
            avenue=data.get("CADDE") or roads.avenue,
            point_name=data.get("NOKTA_ADI"),
            address=data.get("ACIK_ADRES"),
            description=data.get("ACIKLAMA"),
            latitude=None if latitude is None else float(latitude),
            longitude=None if longitude is None else float(longitude),
            active=active,
            warnings=tuple(message.message for message in row.validation.warnings),
        )

    def _apply_plan(
        self,
        session: Session,
        plan: ServiceRouteImportPlan,
    ) -> ServiceRouteImportResult:
        repository = RoutePointRepository(session)
        affected_shift_ids: set[int] = set()
        added = 0
        for entry in plan.entries:
            if entry.action is not ImportAction.ADD:
                continue
            if (
                entry.record is None
                or entry.service_shift_id is None
                or entry.assigned_sequence is None
            ):
                raise RuntimeError("Güzergâh aktarım planı eksik kayıt içeriyor.")
            record = entry.record
            point = repository.add(
                ServisGuzergahNoktasi(
                    servis_vardiya_id=entry.service_shift_id,
                    sira_no=entry.assigned_sequence,
                    il=record.city,
                    ilce=record.district,
                    mahalle=record.neighborhood,
                    sokak=record.street,
                    cadde=record.avenue,
                    nokta_adi=record.point_name,
                    acik_adres=record.address,
                    aciklama=record.description,
                    enlem=record.latitude,
                    boylam=record.longitude,
                    aktif_mi=record.active,
                    durak_mi=False,
                    koordinat_durumu=(
                        "MEVCUT"
                        if record.latitude is not None and record.longitude is not None
                        else "EKSIK"
                    ),
                    koordinat_adres_ozeti=(
                        AddressInput(
                            address=record.address,
                            city=record.city,
                            district=record.district,
                            neighborhood=record.neighborhood,
                            street=record.street,
                            avenue=record.avenue,
                        ).signature()
                        if record.latitude is not None and record.longitude is not None
                        else None
                    ),
                )
            )
            affected_shift_ids.add(entry.service_shift_id)
            added += 1
            self._after_point_added(session, point, added)
        geometry_repository = RouteGeometryRepository(session)
        for shift_id in affected_shift_ids:
            geometry_repository.mark_stale(shift_id)
        return ServiceRouteImportResult(
            added_count=added,
            skipped_count=plan.skip_count,
            invalid_count=plan.invalid_count,
            duplicate_count=plan.duplicate_count,
            ambiguous_count=plan.ambiguous_count,
            missing_service_count=plan.missing_service_count,
        )

    def _after_point_added(
        self,
        session: Session,
        point: ServisGuzergahNoktasi,
        added_count: int,
    ) -> None:
        """Transaction rollback testleri için güvenli genişletme noktası."""

    @staticmethod
    def _point_key(*values: object) -> tuple[str, ...]:
        return tuple(comparison_key(value) or "" for value in values)
