"""Fiziksel servis, servis vardiyası ve şoför yönetimi iş kuralları."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from database.models import Servis, ServisVardiyasi, Sofor, VardiyaTuru, YakaTuru
from database.repositories import (
    AssignmentRepository,
    ServisRepository,
    ServisVardiyasiRepository,
    SoforRepository,
    delete_unused_service,
    service_historical_assignment_count,
)
from database.session import SessionFactory, session_scope
from normalization.text import is_excel_error, normalize_text
from normalization.values import (
    ValueNormalizationError,
    normalize_collar,
    normalize_positive_integer,
    normalize_service_code,
    normalize_shift,
)


logger = logging.getLogger(__name__)


class ServiceManagementError(RuntimeError):
    """Kullanıcıya güvenli biçimde gösterilebilecek temel yönetim hatası."""


class ServiceNotFoundError(ServiceManagementError):
    """Fiziksel servis bulunamadığında üretilir."""


class ServiceShiftNotFoundError(ServiceManagementError):
    """Servis vardiyası bulunamadığında üretilir."""


class DriverNotFoundError(ServiceManagementError):
    """Şoför bulunamadığında üretilir."""


class DuplicateServiceCodeError(ServiceManagementError):
    """Servis kodu daha önce kullanılmışsa üretilir."""


class DuplicateServiceShiftError(ServiceManagementError):
    """Servis-vardiya-yaka birleşimi daha önce kullanılmışsa üretilir."""


class ServiceManagementValidationError(ServiceManagementError):
    """Form alanları iş kurallarına uymadığında üretilir."""


class CapacityBelowOccupancyError(ServiceManagementError):
    """Kapasite aktif doluluğun altına indirilmek istendiğinde üretilir."""


class DriverScheduleConflictError(ServiceManagementError):
    """Bir şoföre çakışan aktif servis görevleri verildiğinde üretilir."""


@dataclass(frozen=True, slots=True)
class ServiceDeleteResult:
    physical_deleted: bool
    deactivated: bool
    message: str


@dataclass(frozen=True, slots=True)
class ServiceFormData:
    service_code: str
    driver_id: int | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class ServiceShiftFormData:
    shift: str
    collar: str
    capacity: int | str
    route_name: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class DriverFormData:
    first_name: str
    last_name: str
    phone: str | None = None
    active: bool = True
    linked_service_id: int | None = None


@dataclass(frozen=True, slots=True)
class ServiceView:
    id: int
    service_code: str
    driver_id: int | None
    driver_name: str | None
    active: bool
    shift_count: int


@dataclass(frozen=True, slots=True)
class ServiceShiftView:
    id: int
    service_id: int
    service_code: str
    shift: str
    collar: str
    capacity: int
    route_name: str | None
    active: bool
    active_assignment_count: int


@dataclass(frozen=True, slots=True)
class AssignedPersonnelView:
    assignment_id: int
    personnel_id: int
    personnel_number: str
    personnel_name: str
    route_name: str | None
    distance_meters: float | None
    pickup_latitude: float | None
    pickup_longitude: float | None
    assignment_date: datetime


@dataclass(frozen=True, slots=True)
class DriverView:
    id: int
    first_name: str | None
    last_name: str | None
    full_name: str
    phone: str | None
    active: bool
    service_ids: tuple[int, ...]
    service_codes: tuple[str, ...]


class ServiceManagementService:
    """Arayüzden bağımsız servis, vardiya ve şoför yönetimi servisi."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
    ) -> None:
        self.session_factory = session_factory

    def list_services(self, search_text: str = "") -> tuple[ServiceView, ...]:
        try:
            with self.session_factory() as session:
                services = ServisRepository(session).search_by_code(search_text)
                return tuple(self._to_service_view(item) for item in services)
        except Exception as error:
            logger.exception("Servis listesi alınamadı.")
            raise ServiceManagementError("Servis listesi alınamadı.") from error

    def get_service(self, service_id: int) -> ServiceView:
        try:
            with self.session_factory() as session:
                service = ServisRepository(session).get_detail(service_id)
                if service is None:
                    raise ServiceNotFoundError("Servis kaydı bulunamadı.")
                return self._to_service_view(service)
        except ServiceNotFoundError:
            raise
        except Exception as error:
            logger.exception("Servis bilgileri alınamadı: servis_id=%s", service_id)
            raise ServiceManagementError("Servis bilgileri alınamadı.") from error

    def list_assigned_personnel(
        self,
        shift_id: int,
    ) -> tuple[AssignedPersonnelView, ...]:
        try:
            with self.session_factory() as session:
                records = AssignmentRepository(session).list_active_for_shift(shift_id)
                return tuple(
                    AssignedPersonnelView(
                        assignment_id=item.assignment_id,
                        personnel_id=item.personnel_id,
                        personnel_number=item.personnel_number,
                        personnel_name=item.personnel_name,
                        route_name=item.route_name,
                        distance_meters=item.distance_meters,
                        pickup_latitude=item.pickup_latitude,
                        pickup_longitude=item.pickup_longitude,
                        assignment_date=item.assignment_date,
                    )
                    for item in records
                )
        except Exception as error:
            logger.exception(
                "Servis-vardiya personel listesi alınamadı: vardiya_id=%s",
                shift_id,
            )
            raise ServiceManagementError(
                "Servise atanmış personel listesi alınamadı."
            ) from error

    def delete_service_controlled(self, service_id: int) -> ServiceDeleteResult:
        """Kullanılmamış servisi siler, geçmiş ilişkili servisi pasifleştirir."""
        try:
            with session_scope(self.session_factory) as session:
                service = ServisRepository(session).get_detail(service_id)
                if service is None:
                    raise ServiceNotFoundError("Servis kaydı bulunamadı.")
                if service_historical_assignment_count(session, service_id):
                    service.aktif_mi = False
                    for shift in service.vardiyalar:
                        shift.aktif_mi = False
                    return ServiceDeleteResult(
                        physical_deleted=False,
                        deactivated=True,
                        message=(
                            "Bu servis atama/geçmiş ilişkisi içerdiği için silinmedi; "
                            "servis ve vardiyaları pasif yapıldı."
                        ),
                    )
                delete_unused_service(session, service)
                return ServiceDeleteResult(
                    physical_deleted=True,
                    deactivated=False,
                    message="Kullanılmamış servis kalıcı olarak silindi.",
                )
        except (ServiceNotFoundError, ServiceManagementError):
            raise
        except Exception as error:
            logger.exception("Kontrollü servis silme işlemi başarısız oldu.")
            raise ServiceManagementError(
                "Servis silinemedi. Yapılan işlem geri alındı."
            ) from error

    def create_service(
        self,
        form_data: ServiceFormData,
        first_shift: ServiceShiftFormData | None = None,
    ) -> ServiceView:
        service_code = self._normalize_service_code(form_data.service_code)
        driver_id = self._normalize_optional_id(form_data.driver_id, "Şoför")
        active = self._normalize_active(form_data.active)
        normalized_shift = (
            self._normalize_shift_form(first_shift) if first_shift is not None else None
        )
        try:
            with session_scope(self.session_factory) as session:
                service_repository = ServisRepository(session)
                if service_repository.get_by_code(service_code) is not None:
                    raise DuplicateServiceCodeError(
                        "Bu servis koduyla kayıtlı bir fiziksel servis zaten bulunuyor."
                    )
                if driver_id is not None:
                    self._require_driver(session, driver_id)
                    target_shifts = (
                        {normalized_shift["shift"]}
                        if normalized_shift is not None
                        and normalized_shift["active"]
                        and active
                        else set()
                    )
                    self._ensure_no_driver_conflict(
                        session,
                        driver_id,
                        current_service_id=None,
                        target_active_shifts=target_shifts,
                    )
                service = service_repository.add(
                    Servis(
                        servis_kodu=service_code,
                        sofor_id=driver_id,
                        aktif_mi=active,
                    )
                )
                self._after_service_created(session, service)
                if normalized_shift is not None:
                    ServisVardiyasiRepository(session).add(
                        ServisVardiyasi(
                            servis_id=service.id,
                            vardiya_turu=normalized_shift["shift"],
                            yaka_turu=normalized_shift["collar"],
                            kapasite=normalized_shift["capacity"],
                            rota_adi=normalized_shift["route_name"],
                            aktif_mi=normalized_shift["active"],
                        )
                    )
                stored = service_repository.get_detail(service.id)
                if stored is None:
                    raise RuntimeError("Oluşturulan servis tekrar okunamadı.")
                result = self._to_service_view(stored)
            logger.info(
                "Manuel servis kaydı oluşturuldu: servis_id=%s, ilk_vardiya=%s",
                result.id,
                first_shift is not None,
            )
            return result
        except (
            DuplicateServiceCodeError,
            DriverNotFoundError,
            DriverScheduleConflictError,
            ServiceManagementValidationError,
        ):
            raise
        except IntegrityError as error:
            logger.warning("Manuel servis kaydında benzersiz kod çakışması oluştu.")
            raise DuplicateServiceCodeError(
                "Bu servis koduyla kayıtlı bir fiziksel servis zaten bulunuyor."
            ) from error
        except Exception as error:
            logger.exception("Manuel servis ve ilk vardiya transaction işlemi geri alındı.")
            raise ServiceManagementError(
                "Servis kaydı oluşturulamadı. Yapılan işlemler geri alındı."
            ) from error

    def update_service(
        self,
        service_id: int,
        form_data: ServiceFormData,
    ) -> ServiceView:
        driver_id = self._normalize_optional_id(form_data.driver_id, "Şoför")
        active = self._normalize_active(form_data.active)
        try:
            with session_scope(self.session_factory) as session:
                repository = ServisRepository(session)
                service = repository.get_detail(service_id)
                if service is None:
                    raise ServiceNotFoundError("Güncellenecek servis bulunamadı.")
                if driver_id is not None:
                    self._require_driver(session, driver_id)
                    active_shifts = {
                        shift.vardiya_turu
                        for shift in service.vardiyalar
                        if shift.aktif_mi and active
                    }
                    self._ensure_no_driver_conflict(
                        session,
                        driver_id,
                        current_service_id=service.id,
                        target_active_shifts=active_shifts,
                    )
                service.sofor_id = driver_id
                service.aktif_mi = active
                repository.session.flush()
                stored = repository.get_detail(service.id)
                if stored is None:
                    raise RuntimeError("Güncellenen servis tekrar okunamadı.")
                result = self._to_service_view(stored)
            logger.info("Fiziksel servis güncellendi: servis_id=%s", service_id)
            return result
        except (
            ServiceNotFoundError,
            DriverNotFoundError,
            DriverScheduleConflictError,
            ServiceManagementValidationError,
        ):
            raise
        except Exception as error:
            logger.exception("Fiziksel servis güncellenemedi: servis_id=%s", service_id)
            raise ServiceManagementError(
                "Servis kaydı güncellenemedi. İşlem geri alındı."
            ) from error

    def set_service_active(self, service_id: int, active: bool) -> ServiceView:
        current = self.get_service(service_id)
        return self.update_service(
            service_id,
            ServiceFormData(
                service_code=current.service_code,
                driver_id=current.driver_id,
                active=active,
            ),
        )

    def list_service_shifts(
        self,
        service_id: int | None = None,
    ) -> tuple[ServiceShiftView, ...]:
        try:
            with self.session_factory() as session:
                repository = ServisVardiyasiRepository(session)
                shifts = repository.list_ordered(service_id=service_id)
                return tuple(
                    self._to_shift_view(
                        item,
                        repository.active_assignment_count(item.id),
                    )
                    for item in shifts
                )
        except Exception as error:
            logger.exception("Servis vardiyası listesi alınamadı.")
            raise ServiceManagementError("Servis vardiyası listesi alınamadı.") from error

    def get_service_shift(self, shift_id: int) -> ServiceShiftView:
        try:
            with self.session_factory() as session:
                repository = ServisVardiyasiRepository(session)
                shift = repository.get_detail(shift_id)
                if shift is None:
                    raise ServiceShiftNotFoundError("Servis vardiyası bulunamadı.")
                return self._to_shift_view(
                    shift,
                    repository.active_assignment_count(shift.id),
                )
        except ServiceShiftNotFoundError:
            raise
        except Exception as error:
            logger.exception("Servis vardiyası alınamadı: vardiya_id=%s", shift_id)
            raise ServiceManagementError("Servis vardiyası alınamadı.") from error

    def create_service_shift(
        self,
        service_id: int,
        form_data: ServiceShiftFormData,
    ) -> ServiceShiftView:
        normalized = self._normalize_shift_form(form_data)
        try:
            with session_scope(self.session_factory) as session:
                service_repository = ServisRepository(session)
                shift_repository = ServisVardiyasiRepository(session)
                service = service_repository.get_detail(service_id)
                if service is None:
                    raise ServiceNotFoundError("Vardiya eklenecek servis bulunamadı.")
                if shift_repository.get_by_identity(
                    service.id,
                    normalized["shift"],
                    normalized["collar"],
                ) is not None:
                    raise DuplicateServiceShiftError(
                        "Bu servis, vardiya ve yaka birleşimi zaten kayıtlı."
                    )
                target_shifts = {
                    item.vardiya_turu for item in service.vardiyalar if item.aktif_mi
                }
                if normalized["active"]:
                    target_shifts.add(normalized["shift"])
                if service.sofor_id is not None and service.aktif_mi:
                    self._ensure_no_driver_conflict(
                        session,
                        service.sofor_id,
                        current_service_id=service.id,
                        target_active_shifts=target_shifts,
                    )
                shift = shift_repository.add(
                    ServisVardiyasi(
                        servis_id=service.id,
                        vardiya_turu=normalized["shift"],
                        yaka_turu=normalized["collar"],
                        kapasite=normalized["capacity"],
                        rota_adi=normalized["route_name"],
                        aktif_mi=normalized["active"],
                    )
                )
                stored = shift_repository.get_detail(shift.id)
                if stored is None:
                    raise RuntimeError("Oluşturulan vardiya tekrar okunamadı.")
                result = self._to_shift_view(stored, 0)
            logger.info("Servis vardiyası oluşturuldu: vardiya_id=%s", result.id)
            return result
        except (
            ServiceNotFoundError,
            DuplicateServiceShiftError,
            DriverScheduleConflictError,
            ServiceManagementValidationError,
        ):
            raise
        except IntegrityError as error:
            logger.warning("Servis vardiyası benzersiz alan çakışması oluştu.")
            raise DuplicateServiceShiftError(
                "Bu servis, vardiya ve yaka birleşimi zaten kayıtlı."
            ) from error
        except Exception as error:
            logger.exception("Servis vardiyası oluşturulamadı.")
            raise ServiceManagementError(
                "Servis vardiyası oluşturulamadı. İşlem geri alındı."
            ) from error

    def update_service_shift(
        self,
        shift_id: int,
        form_data: ServiceShiftFormData,
    ) -> ServiceShiftView:
        normalized = self._normalize_shift_form(form_data)
        try:
            with session_scope(self.session_factory) as session:
                shift_repository = ServisVardiyasiRepository(session)
                service_repository = ServisRepository(session)
                shift = shift_repository.get_detail(shift_id)
                if shift is None:
                    raise ServiceShiftNotFoundError("Güncellenecek vardiya bulunamadı.")
                duplicate = shift_repository.get_by_identity(
                    shift.servis_id,
                    normalized["shift"],
                    normalized["collar"],
                )
                if duplicate is not None and duplicate.id != shift.id:
                    raise DuplicateServiceShiftError(
                        "Bu servis, vardiya ve yaka birleşimi zaten kayıtlı."
                    )
                occupancy = shift_repository.active_assignment_count(shift.id)
                if normalized["capacity"] < occupancy:
                    raise CapacityBelowOccupancyError(
                        f"Kapasite aktif doluluğun ({occupancy}) altına indirilemez."
                    )
                service = service_repository.get_detail(shift.servis_id)
                if service is None:
                    raise ServiceNotFoundError("Vardiyanın fiziksel servisi bulunamadı.")
                target_shifts = {
                    item.vardiya_turu
                    for item in service.vardiyalar
                    if item.id != shift.id and item.aktif_mi
                }
                if normalized["active"]:
                    target_shifts.add(normalized["shift"])
                if service.sofor_id is not None and service.aktif_mi:
                    self._ensure_no_driver_conflict(
                        session,
                        service.sofor_id,
                        current_service_id=service.id,
                        target_active_shifts=target_shifts,
                    )
                shift.vardiya_turu = normalized["shift"]
                shift.yaka_turu = normalized["collar"]
                shift.kapasite = normalized["capacity"]
                shift.rota_adi = normalized["route_name"]
                shift.aktif_mi = normalized["active"]
                shift_repository.session.flush()
                stored = shift_repository.get_detail(shift.id)
                if stored is None:
                    raise RuntimeError("Güncellenen vardiya tekrar okunamadı.")
                result = self._to_shift_view(stored, occupancy)
            logger.info("Servis vardiyası güncellendi: vardiya_id=%s", shift_id)
            return result
        except (
            ServiceNotFoundError,
            ServiceShiftNotFoundError,
            DuplicateServiceShiftError,
            CapacityBelowOccupancyError,
            DriverScheduleConflictError,
            ServiceManagementValidationError,
        ):
            raise
        except IntegrityError as error:
            logger.warning("Vardiya güncellemesinde benzersiz alan çakışması oluştu.")
            raise DuplicateServiceShiftError(
                "Bu servis, vardiya ve yaka birleşimi zaten kayıtlı."
            ) from error
        except Exception as error:
            logger.exception("Servis vardiyası güncellenemedi: vardiya_id=%s", shift_id)
            raise ServiceManagementError(
                "Servis vardiyası güncellenemedi. İşlem geri alındı."
            ) from error

    def set_service_shift_active(
        self,
        shift_id: int,
        active: bool,
    ) -> ServiceShiftView:
        current = self.get_service_shift(shift_id)
        return self.update_service_shift(
            shift_id,
            ServiceShiftFormData(
                shift=current.shift,
                collar=current.collar,
                capacity=current.capacity,
                route_name=current.route_name,
                active=active,
            ),
        )

    def list_drivers(self) -> tuple[DriverView, ...]:
        try:
            with self.session_factory() as session:
                drivers = SoforRepository(session).list_ordered()
                return tuple(self._to_driver_view(item) for item in drivers)
        except Exception as error:
            logger.exception("Şoför listesi alınamadı.")
            raise ServiceManagementError("Şoför listesi alınamadı.") from error

    def get_driver(self, driver_id: int) -> DriverView:
        try:
            with self.session_factory() as session:
                driver = SoforRepository(session).get_with_services(driver_id)
                if driver is None:
                    raise DriverNotFoundError("Şoför kaydı bulunamadı.")
                return self._to_driver_view(driver)
        except DriverNotFoundError:
            raise
        except Exception as error:
            logger.exception("Şoför bilgileri alınamadı: sofor_id=%s", driver_id)
            raise ServiceManagementError("Şoför bilgileri alınamadı.") from error

    def create_driver(self, form_data: DriverFormData) -> DriverView:
        normalized = self._normalize_driver_form(form_data)
        try:
            with session_scope(self.session_factory) as session:
                repository = SoforRepository(session)
                driver = repository.add(
                    Sofor(
                        ad=normalized["first_name"],
                        soyad=normalized["last_name"],
                        ad_soyad=normalized["full_name"],
                        telefon=normalized["phone"],
                        aktif_mi=normalized["active"],
                    )
                )
                if normalized["linked_service_id"] is not None:
                    self._link_driver_to_service(
                        session,
                        driver.id,
                        normalized["linked_service_id"],
                    )
                stored = repository.get_with_services(driver.id)
                if stored is None:
                    raise RuntimeError("Oluşturulan şoför tekrar okunamadı.")
                result = self._to_driver_view(stored)
            logger.info("Şoför kaydı oluşturuldu: sofor_id=%s", result.id)
            return result
        except (
            ServiceNotFoundError,
            DriverScheduleConflictError,
            ServiceManagementValidationError,
        ):
            raise
        except Exception as error:
            logger.exception("Şoför kaydı oluşturulamadı.")
            raise ServiceManagementError(
                "Şoför kaydı oluşturulamadı. İşlem geri alındı."
            ) from error

    def update_driver(
        self,
        driver_id: int,
        form_data: DriverFormData,
    ) -> DriverView:
        normalized = self._normalize_driver_form(form_data)
        try:
            with session_scope(self.session_factory) as session:
                repository = SoforRepository(session)
                driver = repository.get_with_services(driver_id)
                if driver is None:
                    raise DriverNotFoundError("Güncellenecek şoför bulunamadı.")
                if normalized["active"]:
                    self._ensure_driver_schedule_is_consistent(driver)
                driver.ad = normalized["first_name"]
                driver.soyad = normalized["last_name"]
                driver.ad_soyad = normalized["full_name"]
                driver.telefon = normalized["phone"]
                driver.aktif_mi = normalized["active"]
                if normalized["linked_service_id"] is not None:
                    self._link_driver_to_service(
                        session,
                        driver.id,
                        normalized["linked_service_id"],
                    )
                repository.session.flush()
                stored = repository.get_with_services(driver.id)
                if stored is None:
                    raise RuntimeError("Güncellenen şoför tekrar okunamadı.")
                result = self._to_driver_view(stored)
            logger.info("Şoför kaydı güncellendi: sofor_id=%s", driver_id)
            return result
        except (
            DriverNotFoundError,
            ServiceNotFoundError,
            DriverScheduleConflictError,
            ServiceManagementValidationError,
        ):
            raise
        except Exception as error:
            logger.exception("Şoför kaydı güncellenemedi: sofor_id=%s", driver_id)
            raise ServiceManagementError(
                "Şoför kaydı güncellenemedi. İşlem geri alındı."
            ) from error

    def set_driver_active(self, driver_id: int, active: bool) -> DriverView:
        normalized_active = self._normalize_active(active)
        try:
            with session_scope(self.session_factory) as session:
                repository = SoforRepository(session)
                driver = repository.get_with_services(driver_id)
                if driver is None:
                    raise DriverNotFoundError("Şoför kaydı bulunamadı.")
                if normalized_active:
                    self._ensure_driver_schedule_is_consistent(driver)
                repository.set_active(driver, normalized_active)
                stored = repository.get_with_services(driver.id)
                if stored is None:
                    raise RuntimeError("Şoför tekrar okunamadı.")
                result = self._to_driver_view(stored)
            logger.info(
                "Şoför aktiflik durumu güncellendi: sofor_id=%s, aktif=%s",
                driver_id,
                normalized_active,
            )
            return result
        except (DriverNotFoundError, DriverScheduleConflictError):
            raise
        except Exception as error:
            logger.exception("Şoför aktiflik durumu güncellenemedi: sofor_id=%s", driver_id)
            raise ServiceManagementError(
                "Şoför aktiflik durumu güncellenemedi. İşlem geri alındı."
            ) from error

    def link_driver_to_service(self, driver_id: int, service_id: int) -> ServiceView:
        try:
            with session_scope(self.session_factory) as session:
                self._link_driver_to_service(session, driver_id, service_id)
                service = ServisRepository(session).get_detail(service_id)
                if service is None:
                    raise RuntimeError("İlişkilendirilen servis tekrar okunamadı.")
                result = self._to_service_view(service)
            logger.info(
                "Şoför servisle ilişkilendirildi: sofor_id=%s, servis_id=%s",
                driver_id,
                service_id,
            )
            return result
        except (
            DriverNotFoundError,
            ServiceNotFoundError,
            DriverScheduleConflictError,
        ):
            raise
        except Exception as error:
            logger.exception("Şoför-servis ilişkisi kurulamadı.")
            raise ServiceManagementError(
                "Şoför servisle ilişkilendirilemedi. İşlem geri alındı."
            ) from error

    def _link_driver_to_service(
        self,
        session: Session,
        driver_id: int,
        service_id: int,
    ) -> None:
        self._require_driver(session, driver_id)
        repository = ServisRepository(session)
        service = repository.get_detail(service_id)
        if service is None:
            raise ServiceNotFoundError("İlişkilendirilecek servis bulunamadı.")
        target_shifts = {
            item.vardiya_turu
            for item in service.vardiyalar
            if item.aktif_mi and service.aktif_mi
        }
        self._ensure_no_driver_conflict(
            session,
            driver_id,
            current_service_id=service.id,
            target_active_shifts=target_shifts,
        )
        service.sofor_id = driver_id
        repository.session.flush()

    def _require_driver(self, session: Session, driver_id: int) -> Sofor:
        driver = SoforRepository(session).get_with_services(driver_id)
        if driver is None:
            raise DriverNotFoundError("Seçilen şoför bulunamadı.")
        return driver

    def _ensure_no_driver_conflict(
        self,
        session: Session,
        driver_id: int,
        *,
        current_service_id: int | None,
        target_active_shifts: set[VardiyaTuru],
    ) -> None:
        if not target_active_shifts:
            return
        driver = self._require_driver(session, driver_id)
        for other_service in driver.servisler:
            if other_service.id == current_service_id or not other_service.aktif_mi:
                continue
            other_shifts = {
                shift.vardiya_turu
                for shift in other_service.vardiyalar
                if shift.aktif_mi
            }
            overlap = target_active_shifts & other_shifts
            if overlap:
                shift_text = ", ".join(sorted(item.value for item in overlap))
                raise DriverScheduleConflictError(
                    f"Şoförün {other_service.servis_kodu} servisinde çakışan aktif "
                    f"vardiyası bulunuyor: {shift_text}."
                )

    @staticmethod
    def _ensure_driver_schedule_is_consistent(driver: Sofor) -> None:
        service_by_shift: dict[VardiyaTuru, str] = {}
        for service in driver.servisler:
            if not service.aktif_mi:
                continue
            for shift in service.vardiyalar:
                if not shift.aktif_mi:
                    continue
                previous_service = service_by_shift.get(shift.vardiya_turu)
                if previous_service is not None and previous_service != service.servis_kodu:
                    raise DriverScheduleConflictError(
                        f"Şoförün {previous_service} ve {service.servis_kodu} "
                        f"servislerinde {shift.vardiya_turu.value} vardiyası çakışıyor."
                    )
                service_by_shift[shift.vardiya_turu] = service.servis_kodu

    @staticmethod
    def _normalize_service_code_value(value: object) -> str:
        try:
            return normalize_service_code(value)
        except ValueNormalizationError as error:
            raise ServiceManagementValidationError(error.message) from error

    def _normalize_service_code(self, value: object) -> str:
        return self._normalize_service_code_value(value)

    @staticmethod
    def _normalize_optional_id(value: object, label: str) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ServiceManagementValidationError(f"{label} seçimi geçersiz.")
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise ServiceManagementValidationError(f"{label} seçimi geçersiz.") from None
        if result <= 0:
            raise ServiceManagementValidationError(f"{label} seçimi geçersiz.")
        return result

    @staticmethod
    def _normalize_active(value: object) -> bool:
        if not isinstance(value, bool):
            raise ServiceManagementValidationError("Aktiflik değeri geçersiz.")
        return value

    @classmethod
    def _normalize_shift_form(cls, form_data: ServiceShiftFormData) -> dict[str, object]:
        try:
            shift = VardiyaTuru(normalize_shift(form_data.shift))
            collar = YakaTuru(normalize_collar(form_data.collar))
            capacity = normalize_positive_integer(form_data.capacity)
        except ValueNormalizationError as error:
            raise ServiceManagementValidationError(error.message) from error
        route_name = normalize_text(form_data.route_name)
        if route_name is not None and is_excel_error(route_name):
            raise ServiceManagementValidationError("Rota adı Excel hata değeri içeremez.")
        return {
            "shift": shift,
            "collar": collar,
            "capacity": capacity,
            "route_name": route_name,
            "active": cls._normalize_active(form_data.active),
        }

    @classmethod
    def _normalize_driver_form(cls, form_data: DriverFormData) -> dict[str, object]:
        first_name = normalize_text(form_data.first_name)
        last_name = normalize_text(form_data.last_name)
        phone = normalize_text(form_data.phone)
        if first_name is None:
            raise ServiceManagementValidationError("Şoför adı boş olamaz.")
        if last_name is None:
            raise ServiceManagementValidationError("Şoför soyadı boş olamaz.")
        for value, label in ((first_name, "Şoför adı"), (last_name, "Şoför soyadı")):
            if is_excel_error(value):
                raise ServiceManagementValidationError(
                    f"{label} Excel hata değeri içeremez."
                )
        return {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": f"{first_name} {last_name}",
            "phone": phone,
            "active": cls._normalize_active(form_data.active),
            "linked_service_id": cls._normalize_optional_id(
                form_data.linked_service_id,
                "Servis",
            ),
        }

    def _after_service_created(self, session: Session, service: Servis) -> None:
        """Transaction geri alma testleri için güvenli genişletme noktası."""

    @staticmethod
    def _to_service_view(service: Servis) -> ServiceView:
        return ServiceView(
            id=service.id,
            service_code=service.servis_kodu,
            driver_id=service.sofor_id,
            driver_name=service.sofor.ad_soyad if service.sofor is not None else None,
            active=service.aktif_mi,
            shift_count=len(service.vardiyalar),
        )

    @staticmethod
    def _to_shift_view(
        shift: ServisVardiyasi,
        active_assignment_count: int,
    ) -> ServiceShiftView:
        return ServiceShiftView(
            id=shift.id,
            service_id=shift.servis_id,
            service_code=shift.servis.servis_kodu,
            shift=shift.vardiya_turu.value,
            collar=shift.yaka_turu.value,
            capacity=shift.kapasite,
            route_name=shift.rota_adi,
            active=shift.aktif_mi,
            active_assignment_count=active_assignment_count,
        )

    @staticmethod
    def _to_driver_view(driver: Sofor) -> DriverView:
        services = sorted(driver.servisler, key=lambda item: item.servis_kodu)
        return DriverView(
            id=driver.id,
            first_name=driver.ad,
            last_name=driver.soyad,
            full_name=driver.ad_soyad,
            phone=driver.telefon,
            active=driver.aktif_mi,
            service_ids=tuple(item.id for item in services),
            service_codes=tuple(item.servis_kodu for item in services),
        )
