"""Manuel personel yönetimi iş kuralları ve transaction işlemleri."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from database.models import Personel, PersonelTuru, VardiyaTuru, YakaTuru
from database.repositories import PersonelRepository
from database.session import SessionFactory, session_scope
from normalization import extract_address_roads, validate_personnel_row
from services.geocoding import AddressInput


logger = logging.getLogger(__name__)


class PersonnelManagementError(RuntimeError):
    """Kullanıcıya güvenli biçimde gösterilebilecek temel personel hatası."""


class PersonnelNotFoundError(PersonnelManagementError):
    """İstenen personel bulunamadığında üretilir."""


class DuplicatePersonnelNumberError(PersonnelManagementError):
    """Sicil numarası daha önce kullanılmışsa üretilir."""


class PersonnelValidationError(PersonnelManagementError):
    def __init__(self, messages: tuple[str, ...]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


class PersonnelWarningsRequireConfirmation(PersonnelManagementError):
    def __init__(self, messages: tuple[str, ...]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


@dataclass(frozen=True, slots=True)
class PersonnelFormData:
    personnel_number: str
    first_name: str | None
    last_name: str | None
    address: str
    city: str
    district: str
    neighborhood: str
    shift: str
    collar: str
    street: str | None = None
    avenue: str | None = None
    personnel_type: str = PersonelTuru.KADROLU.value
    internship_start: date | str | None = None
    internship_end: date | str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class PersonnelView:
    id: int
    personnel_number: str
    first_name: str | None
    last_name: str | None
    address: str
    city: str | None
    district: str | None
    neighborhood: str | None
    street: str | None
    avenue: str | None
    shift: str
    collar: str
    active: bool
    latitude: float | None
    longitude: float | None
    personnel_type: str
    internship_start: date | None
    internship_end: date | None
    import_group_code: str | None


@dataclass(frozen=True, slots=True)
class PersonnelSaveResult:
    personnel: PersonnelView
    warnings: tuple[str, ...]
    assignment_review_recommended: bool = False


class PersonnelManagementService:
    """Arayüzden bağımsız manuel personel ekleme ve güncelleme servisi."""

    REASSESSMENT_FIELDS = {
        "ACIK_ADRES",
        "IL",
        "ILCE",
        "MAHALLE",
        "SOKAK",
        "CADDE",
        "VARDIYA",
        "YAKA",
    }

    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
    ) -> None:
        self.session_factory = session_factory

    def list_personnel(self) -> tuple[PersonnelView, ...]:
        try:
            with self.session_factory() as session:
                repository = PersonelRepository(session)
                return tuple(self._to_view(item) for item in repository.list_ordered())
        except Exception as error:
            logger.exception("Personel listesi alınamadı.")
            raise PersonnelManagementError("Personel listesi alınamadı.") from error

    def search_by_personnel_number(
        self,
        search_text: str,
    ) -> tuple[PersonnelView, ...]:
        try:
            with self.session_factory() as session:
                repository = PersonelRepository(session)
                return tuple(
                    self._to_view(item)
                    for item in repository.search_by_sicil_no(search_text)
                )
        except Exception as error:
            logger.exception("Sicil numarasıyla personel araması tamamlanamadı.")
            raise PersonnelManagementError(
                "Personel araması tamamlanamadı."
            ) from error

    def get_personnel(self, personnel_id: int) -> PersonnelView:
        try:
            with self.session_factory() as session:
                personnel = PersonelRepository(session).get_by_id(personnel_id)
                if personnel is None:
                    raise PersonnelNotFoundError("Personel kaydı bulunamadı.")
                return self._to_view(personnel)
        except PersonnelNotFoundError:
            raise
        except Exception as error:
            logger.exception("Personel bilgileri alınamadı: personel_id=%s", personnel_id)
            raise PersonnelManagementError("Personel bilgileri alınamadı.") from error

    def create_personnel(
        self,
        form_data: PersonnelFormData,
        *,
        allow_warnings: bool = False,
    ) -> PersonnelSaveResult:
        validation = self._validate(form_data)
        self._raise_for_validation(validation, allow_warnings=allow_warnings)
        normalized = validation.normalized_data
        try:
            with session_scope(self.session_factory) as session:
                repository = PersonelRepository(session)
                personnel_number = str(normalized["SICIL_NO"])
                if repository.get_by_sicil_no(personnel_number) is not None:
                    raise DuplicatePersonnelNumberError(
                        "Bu sicil numarasıyla kayıtlı bir personel zaten bulunuyor."
                    )
                personnel = Personel(
                    sicil_no=personnel_number,
                    acik_adres=str(normalized["ACIK_ADRES"]),
                    vardiya_turu=VardiyaTuru(normalized["VARDIYA"]),
                    yaka_turu=YakaTuru(normalized["YAKA"]),
                    personel_turu=PersonelTuru(normalized["STATU"]),
                    aktarim_grup_kodu="MANUEL",
                )
                self._apply_profile(personnel, normalized)
                repository.add(personnel)
                result = PersonnelSaveResult(
                    personnel=self._to_view(personnel),
                    warnings=self._warning_messages(validation),
                )
            logger.info("Manuel personel kaydı oluşturuldu: personel_id=%s", result.personnel.id)
            return result
        except DuplicatePersonnelNumberError:
            raise
        except IntegrityError as error:
            logger.warning("Manuel personel kaydında benzersiz sicil çakışması oluştu.")
            raise DuplicatePersonnelNumberError(
                "Bu sicil numarasıyla kayıtlı bir personel zaten bulunuyor."
            ) from error
        except Exception as error:
            logger.exception("Manuel personel kaydı oluşturulamadı.")
            raise PersonnelManagementError(
                "Personel kaydı oluşturulamadı. İşlem geri alındı."
            ) from error

    def update_personnel(
        self,
        personnel_id: int,
        form_data: PersonnelFormData,
        *,
        allow_warnings: bool = False,
    ) -> PersonnelSaveResult:
        try:
            with session_scope(self.session_factory) as session:
                repository = PersonelRepository(session)
                personnel = repository.get_by_id(personnel_id)
                if personnel is None:
                    raise PersonnelNotFoundError("Güncellenecek personel bulunamadı.")
                validation = self._validate(form_data, existing=personnel)
                self._raise_for_validation(validation, allow_warnings=allow_warnings)
                normalized = validation.normalized_data
                reassessment_required = self._requires_reassessment(personnel, normalized)
                self._apply_profile(personnel, normalized)
                repository.session.flush()
                result = PersonnelSaveResult(
                    personnel=self._to_view(personnel),
                    warnings=self._warning_messages(validation),
                    assignment_review_recommended=reassessment_required,
                )
            logger.info("Personel kaydı güncellendi: personel_id=%s", personnel_id)
            return result
        except (PersonnelNotFoundError, PersonnelValidationError, PersonnelWarningsRequireConfirmation):
            raise
        except Exception as error:
            logger.exception("Personel kaydı güncellenemedi: personel_id=%s", personnel_id)
            raise PersonnelManagementError(
                "Personel kaydı güncellenemedi. İşlem geri alındı."
            ) from error

    def set_personnel_active(self, personnel_id: int, active: bool) -> PersonnelView:
        try:
            with session_scope(self.session_factory) as session:
                repository = PersonelRepository(session)
                personnel = repository.get_by_id(personnel_id)
                if personnel is None:
                    raise PersonnelNotFoundError("Personel kaydı bulunamadı.")
                repository.set_active(personnel, active)
                result = self._to_view(personnel)
            logger.info(
                "Personel aktiflik durumu güncellendi: personel_id=%s, aktif=%s",
                personnel_id,
                active,
            )
            return result
        except PersonnelNotFoundError:
            raise
        except Exception as error:
            logger.exception(
                "Personel aktiflik durumu güncellenemedi: personel_id=%s",
                personnel_id,
            )
            raise PersonnelManagementError(
                "Personelin aktiflik durumu güncellenemedi. İşlem geri alındı."
            ) from error

    @staticmethod
    def _warning_messages(validation) -> tuple[str, ...]:
        return tuple(message.message for message in validation.warnings)

    @classmethod
    def _raise_for_validation(cls, validation, *, allow_warnings: bool) -> None:
        if validation.errors:
            raise PersonnelValidationError(
                tuple(message.message for message in validation.errors)
            )
        if validation.warnings and not allow_warnings:
            raise PersonnelWarningsRequireConfirmation(
                cls._warning_messages(validation)
            )

    @staticmethod
    def _validation_row(
        form_data: PersonnelFormData,
        existing: Personel | None,
    ) -> dict[str, Any]:
        return {
            "SICIL_NO": existing.sicil_no if existing is not None else form_data.personnel_number,
            "AD": form_data.first_name,
            "SOYAD": form_data.last_name,
            "ACIK_ADRES": form_data.address,
            "IL": form_data.city,
            "ILCE": form_data.district,
            "MAHALLE": form_data.neighborhood,
            "SOKAK": form_data.street,
            "CADDE": form_data.avenue,
            "ENLEM": existing.enlem if existing is not None else None,
            "BOYLAM": existing.boylam if existing is not None else None,
            "VARDIYA": form_data.shift,
            "YAKA": form_data.collar,
            "STATU": form_data.personnel_type,
            "STAJ_BASLAMA": form_data.internship_start,
            "STAJ_BITIS": form_data.internship_end,
            "AKTIF_MI": form_data.active,
        }

    def _validate(
        self,
        form_data: PersonnelFormData,
        *,
        existing: Personel | None = None,
    ):
        return validate_personnel_row(
            1,
            self._validation_row(form_data, existing),
        )

    @staticmethod
    def _apply_profile(personnel: Personel, normalized: dict[str, Any]) -> None:
        previous_address = AddressInput(
            address=personnel.acik_adres,
            city=personnel.il,
            district=personnel.ilce,
            neighborhood=personnel.mahalle,
            street=personnel.sokak,
            avenue=personnel.cadde,
        ).signature()
        extracted_roads = extract_address_roads(normalized["ACIK_ADRES"])
        street = normalized.get("SOKAK") or extracted_roads.street
        avenue = normalized.get("CADDE") or extracted_roads.avenue
        new_address = AddressInput(
            address=str(normalized["ACIK_ADRES"]),
            city=normalized.get("IL"),
            district=normalized.get("ILCE"),
            neighborhood=normalized.get("MAHALLE"),
            street=street,
            avenue=avenue,
        ).signature()
        if (
            personnel.enlem is not None
            and personnel.boylam is not None
            and previous_address != new_address
        ):
            personnel.koordinat_durumu = "GUNCEL_DEGIL"
        personnel.ad = normalized.get("AD")
        personnel.soyad = normalized.get("SOYAD")
        personnel.ad_soyad = " ".join(
            value for value in (personnel.ad, personnel.soyad) if value
        ) or None
        personnel.acik_adres = str(normalized["ACIK_ADRES"])
        personnel.il = normalized.get("IL")
        personnel.ilce = normalized.get("ILCE")
        personnel.mahalle = normalized.get("MAHALLE")
        personnel.sokak = street
        personnel.cadde = avenue
        personnel.vardiya_turu = VardiyaTuru(normalized["VARDIYA"])
        personnel.yaka_turu = YakaTuru(normalized["YAKA"])
        personnel.personel_turu = PersonelTuru(normalized["STATU"])
        personnel.staj_baslangic_tarihi = normalized.get("STAJ_BASLAMA")
        personnel.staj_bitis_tarihi = normalized.get("STAJ_BITIS")
        personnel.aktif_mi = bool(normalized["AKTIF_MI"])

    @classmethod
    def _requires_reassessment(
        cls,
        personnel: Personel,
        normalized: dict[str, Any],
    ) -> bool:
        extracted_roads = extract_address_roads(normalized.get("ACIK_ADRES"))
        candidate_values = {
            **normalized,
            "SOKAK": normalized.get("SOKAK") or extracted_roads.street,
            "CADDE": normalized.get("CADDE") or extracted_roads.avenue,
        }
        current_values = {
            "ACIK_ADRES": personnel.acik_adres,
            "IL": personnel.il,
            "ILCE": personnel.ilce,
            "MAHALLE": personnel.mahalle,
            "SOKAK": personnel.sokak,
            "CADDE": personnel.cadde,
            "VARDIYA": personnel.vardiya_turu.value,
            "YAKA": personnel.yaka_turu.value,
        }
        return any(
            current_values[field_name] != candidate_values.get(field_name)
            for field_name in cls.REASSESSMENT_FIELDS
        )

    @staticmethod
    def _to_view(personnel: Personel) -> PersonnelView:
        return PersonnelView(
            id=personnel.id,
            personnel_number=personnel.sicil_no,
            first_name=personnel.ad,
            last_name=personnel.soyad,
            address=personnel.acik_adres,
            city=personnel.il,
            district=personnel.ilce,
            neighborhood=personnel.mahalle,
            street=personnel.sokak,
            avenue=personnel.cadde,
            shift=personnel.vardiya_turu.value,
            collar=personnel.yaka_turu.value,
            active=personnel.aktif_mi,
            latitude=personnel.enlem,
            longitude=personnel.boylam,
            personnel_type=personnel.personel_turu.value,
            internship_start=personnel.staj_baslangic_tarihi,
            internship_end=personnel.staj_bitis_tarihi,
            import_group_code=personnel.aktarim_grup_kodu,
        )
