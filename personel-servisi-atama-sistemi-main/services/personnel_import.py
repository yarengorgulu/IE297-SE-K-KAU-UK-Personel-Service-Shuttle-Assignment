"""Doğrulanmış personel önizlemesini transaction ile veritabanına aktarır."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from database.models import Personel, PersonelTuru, VardiyaTuru, YakaTuru
from database.repositories import PersonelRepository
from database.session import SessionFactory, session_scope
from excel_import.models import AnalysisReport, PreviewRow
from normalization.address import extract_address_roads
from normalization.result import RecordType
from services.service_capacity_import import ConflictBehavior, ImportAction
from services.geocoding import AddressInput


logger = logging.getLogger(__name__)


class PersonnelImportError(RuntimeError):
    """Kullanıcıya gösterilebilecek personel aktarım hatası."""


@dataclass(frozen=True, slots=True)
class PersonnelImportRecord:
    row_number: int
    personnel_number: str
    first_name: str | None
    last_name: str | None
    first_name_provided: bool
    last_name_provided: bool
    address: str
    city: str
    district: str
    neighborhood: str
    street: str | None
    avenue: str | None
    latitude: float | None
    longitude: float | None
    shift: VardiyaTuru
    collar: YakaTuru
    personnel_type: PersonelTuru
    internship_start: date | None
    internship_end: date | None
    active: bool | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonnelImportPlanEntry:
    row_number: int
    action: ImportAction
    record: PersonnelImportRecord | None = None
    creates_personnel: bool = False
    is_conflict: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PersonnelImportPlan:
    conflict_behavior: ConflictBehavior
    entries: tuple[PersonnelImportPlanEntry, ...]
    valid_record_count: int
    warning_record_count: int

    @property
    def new_personnel_count(self) -> int:
        return sum(entry.creates_personnel for entry in self.entries)

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
class PersonnelImportResult:
    added_count: int
    updated_count: int
    skipped_count: int
    invalid_count: int
    imported_warning_count: int


class PersonnelImportService:
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
    ) -> PersonnelImportPlan:
        self._ensure_personnel_report(report)
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
    ) -> PersonnelImportResult:
        self._ensure_personnel_report(report)
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
                "Personel aktarımı tamamlandı: eklenen=%s, güncellenen=%s, "
                "atlanan=%s, hatalı=%s",
                result.added_count,
                result.updated_count,
                result.skipped_count,
                result.invalid_count,
            )
            return result
        except PersonnelImportError:
            raise
        except Exception as error:
            logger.exception("Personel aktarımı geri alındı.")
            raise PersonnelImportError(
                "Personel aktarımı tamamlanamadı. Yapılan işlemler geri alındı."
            ) from error

    @staticmethod
    def _ensure_personnel_report(report: AnalysisReport) -> None:
        if report.record_type is not RecordType.PERSONEL:
            raise PersonnelImportError(
                "Yalnızca Personel dosyası personel tablosuna aktarılabilir."
            )

    def _build_plan(
        self,
        session: Session,
        report: AnalysisReport,
        conflict_behavior: ConflictBehavior,
        *,
        include_warning_rows: bool,
    ) -> PersonnelImportPlan:
        behavior = ConflictBehavior(conflict_behavior)
        candidate_numbers = {
            str(row.validation.normalized_data.get("SICIL_NO", "")).strip()
            for row in report.rows
            if row.validation.is_valid
        }
        candidate_numbers.discard("")
        repository = PersonelRepository(session)
        existing = {
            personnel.sicil_no: personnel
            for personnel in repository.get_by_sicil_numbers(candidate_numbers)
        }
        known_numbers = set(existing)

        entries: list[PersonnelImportPlanEntry] = []
        valid_record_count = 0
        warning_record_count = 0
        active_column_present = any(
            entry.target_field == "AKTIF_MI" for entry in report.mapping.entries
        )
        first_name_column_present = any(
            entry.target_field == "AD" for entry in report.mapping.entries
        )
        last_name_column_present = any(
            entry.target_field == "SOYAD" for entry in report.mapping.entries
        )
        for row in report.rows:
            if row.validation.errors:
                entries.append(
                    PersonnelImportPlanEntry(
                        row_number=row.row_number,
                        action=ImportAction.INVALID,
                        reason=row.error_text or "Satır doğrulama hatası içeriyor.",
                    )
                )
                continue
            try:
                record = self._record_from_preview(
                    row,
                    active_column_present=active_column_present,
                    first_name_column_present=first_name_column_present,
                    last_name_column_present=last_name_column_present,
                )
            except (TypeError, ValueError) as error:
                entries.append(
                    PersonnelImportPlanEntry(
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
                        PersonnelImportPlanEntry(
                            row_number=row.row_number,
                            action=ImportAction.SKIP,
                            record=record,
                            reason="Uyarılı kayıt için kullanıcı onayı verilmedi.",
                        )
                    )
                    continue

            if record.personnel_number in known_numbers:
                entries.append(
                    PersonnelImportPlanEntry(
                        row_number=row.row_number,
                        action=(
                            ImportAction.UPDATE
                            if behavior is ConflictBehavior.UPDATE
                            else ImportAction.SKIP
                        ),
                        record=record,
                        is_conflict=True,
                        reason=(
                            "Mevcut personel güncellenecek."
                            if behavior is ConflictBehavior.UPDATE
                            else "Mevcut personel korunarak satır atlanacak."
                        ),
                    )
                )
                continue

            known_numbers.add(record.personnel_number)
            entries.append(
                PersonnelImportPlanEntry(
                    row_number=row.row_number,
                    action=ImportAction.ADD,
                    record=record,
                    creates_personnel=True,
                )
            )

        return PersonnelImportPlan(
            conflict_behavior=behavior,
            entries=tuple(entries),
            valid_record_count=valid_record_count,
            warning_record_count=warning_record_count,
        )

    @staticmethod
    def _required_text(data: dict[str, Any], field_name: str, label: str) -> str:
        value = str(data.get(field_name) or "").strip()
        if not value:
            raise ValueError(f"{label} boş olamaz.")
        return value

    @staticmethod
    def _optional_text(data: dict[str, Any], field_name: str) -> str | None:
        raw_value = data.get(field_name)
        if raw_value is None:
            return None
        value = str(raw_value).strip()
        return value or None

    @classmethod
    def _record_from_preview(
        cls,
        row: PreviewRow,
        *,
        active_column_present: bool,
        first_name_column_present: bool,
        last_name_column_present: bool,
    ) -> PersonnelImportRecord:
        data: dict[str, Any] = row.validation.normalized_data
        personnel_number = cls._required_text(data, "SICIL_NO", "Sicil numarası")
        first_name = cls._optional_text(data, "AD")
        last_name = cls._optional_text(data, "SOYAD")
        address = cls._required_text(data, "ACIK_ADRES", "Açık adres")
        city = cls._required_text(data, "IL", "İl")
        district = cls._required_text(data, "ILCE", "İlçe")
        neighborhood = cls._required_text(data, "MAHALLE", "Mahalle")
        roads = extract_address_roads(address)
        street = cls._optional_text(data, "SOKAK") or roads.street
        avenue = cls._optional_text(data, "CADDE") or roads.avenue
        try:
            shift = VardiyaTuru(data.get("VARDIYA"))
        except (TypeError, ValueError):
            raise ValueError("Vardiya değeri geçersiz veya boş.") from None
        try:
            collar = YakaTuru(data.get("YAKA"))
        except (TypeError, ValueError):
            raise ValueError("Yaka türü yalnızca MAVİ veya BEYAZ olabilir.") from None
        try:
            personnel_type = PersonelTuru(data.get("STATU"))
        except (TypeError, ValueError):
            raise ValueError("Personel statüsü geçersiz veya boş.") from None
        active = data.get("AKTIF_MI") if active_column_present else None
        if active is not None and not isinstance(active, bool):
            raise ValueError("Aktiflik değeri geçersiz.")
        latitude = data.get("ENLEM")
        longitude = data.get("BOYLAM")
        if latitude is not None and not isinstance(latitude, (int, float)):
            raise ValueError("Enlem değeri geçersiz.")
        if longitude is not None and not isinstance(longitude, (int, float)):
            raise ValueError("Boylam değeri geçersiz.")
        return PersonnelImportRecord(
            row_number=row.row_number,
            personnel_number=personnel_number,
            first_name=first_name,
            last_name=last_name,
            first_name_provided=first_name_column_present,
            last_name_provided=last_name_column_present,
            address=address,
            city=city,
            district=district,
            neighborhood=neighborhood,
            street=street,
            avenue=avenue,
            latitude=None if latitude is None else float(latitude),
            longitude=None if longitude is None else float(longitude),
            shift=shift,
            collar=collar,
            personnel_type=personnel_type,
            internship_start=data.get("STAJ_BASLAMA"),
            internship_end=data.get("STAJ_BITIS"),
            active=active,
            warnings=tuple(message.message for message in row.validation.warnings),
        )

    def _apply_plan(
        self,
        session: Session,
        plan: PersonnelImportPlan,
    ) -> PersonnelImportResult:
        records = [entry.record for entry in plan.entries if entry.record is not None]
        numbers = {record.personnel_number for record in records}
        repository = PersonelRepository(session)
        personnel_cache = {
            personnel.sicil_no: personnel
            for personnel in repository.get_by_sicil_numbers(numbers)
        }

        added = 0
        updated = 0
        imported_warnings = 0
        for entry in plan.entries:
            if entry.action not in {ImportAction.ADD, ImportAction.UPDATE}:
                continue
            added_entry, updated_entry = self._apply_entry(
                entry,
                repository,
                personnel_cache,
            )
            added += added_entry
            updated += updated_entry
            if entry.record is not None and entry.record.warnings:
                imported_warnings += 1

        return PersonnelImportResult(
            added_count=added,
            updated_count=updated,
            skipped_count=plan.skip_count,
            invalid_count=plan.invalid_count,
            imported_warning_count=imported_warnings,
        )

    def _apply_entry(
        self,
        entry: PersonnelImportPlanEntry,
        repository: PersonelRepository,
        personnel_cache: dict[str, Personel],
    ) -> tuple[int, int]:
        record = entry.record
        if record is None:
            raise RuntimeError("Aktarım satırında personel verisi bulunamadı.")
        personnel = personnel_cache.get(record.personnel_number)
        if entry.action is ImportAction.ADD:
            personnel = Personel(sicil_no=record.personnel_number)
            self._apply_profile(personnel, record, new_record=True)
            repository.add(personnel)
            personnel_cache[record.personnel_number] = personnel
            return 1, 0
        if personnel is None:
            personnel = repository.get_by_sicil_no(record.personnel_number)
        if personnel is None:
            raise RuntimeError("Güncellenecek personel bulunamadı.")
        self._apply_profile(personnel, record, new_record=False)
        repository.session.flush()
        personnel_cache[record.personnel_number] = personnel
        return 0, 1

    @staticmethod
    def _apply_profile(
        personnel: Personel,
        record: PersonnelImportRecord,
        *,
        new_record: bool,
    ) -> None:
        previous_address = AddressInput(
            address=personnel.acik_adres,
            city=personnel.il,
            district=personnel.ilce,
            neighborhood=personnel.mahalle,
            street=personnel.sokak,
            avenue=personnel.cadde,
        ).signature()
        new_address = AddressInput(
            address=record.address,
            city=record.city,
            district=record.district,
            neighborhood=record.neighborhood,
            street=record.street,
            avenue=record.avenue,
        )
        if new_record or record.first_name_provided:
            personnel.ad = record.first_name
        if new_record or record.last_name_provided:
            personnel.soyad = record.last_name
        personnel.ad_soyad = " ".join(
            value for value in (personnel.ad, personnel.soyad) if value
        ) or None
        personnel.acik_adres = record.address
        personnel.il = record.city
        personnel.ilce = record.district
        personnel.mahalle = record.neighborhood
        personnel.sokak = record.street
        personnel.cadde = record.avenue
        if record.latitude is not None and record.longitude is not None:
            personnel.enlem = record.latitude
            personnel.boylam = record.longitude
            personnel.koordinat_durumu = "MEVCUT"
            personnel.koordinat_adres_ozeti = new_address.signature()
        elif new_record or personnel.enlem is None or personnel.boylam is None:
            personnel.enlem = None
            personnel.boylam = None
            personnel.koordinat_durumu = "EKSIK"
            personnel.koordinat_adres_ozeti = None
        elif previous_address != new_address.signature():
            personnel.koordinat_durumu = "GUNCEL_DEGIL"
        personnel.vardiya_turu = record.shift
        personnel.yaka_turu = record.collar
        personnel.personel_turu = record.personnel_type
        personnel.staj_baslangic_tarihi = record.internship_start
        personnel.staj_bitis_tarihi = record.internship_end
        if new_record or record.active is not None:
            personnel.aktif_mi = True if record.active is None else record.active
