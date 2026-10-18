"""Servis-vardiya güzergâh noktaları için manuel yönetim kuralları."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from database.models import ServisGuzergahNoktasi
from database.repositories import (
    RouteGeometryRepository,
    RoutePointRepository,
    ServisVardiyasiRepository,
)
from database.session import SessionFactory, session_scope
from normalization.address import extract_address_roads
from normalization.text import comparison_key, is_blank, is_excel_error, normalize_text
from normalization.values import (
    ValueNormalizationError,
    normalize_coordinate,
    normalize_positive_integer,
)
from services.geocoding import AddressInput


logger = logging.getLogger(__name__)


class RouteManagementError(RuntimeError):
    """Kullanıcıya güvenli biçimde gösterilebilecek temel güzergâh hatası."""


class RoutePointNotFoundError(RouteManagementError):
    """Güzergâh noktası bulunamadığında üretilir."""


class RoutePointValidationError(RouteManagementError):
    """Güzergâh noktası alanları geçersiz olduğunda üretilir."""


class DuplicateRouteSequenceError(RouteManagementError):
    """Aynı servis-vardiyada sıra numarası tekrarlandığında üretilir."""


class DuplicateRoutePointError(RouteManagementError):
    """Aynı güzergâh noktası tekrar eklenmek istendiğinde üretilir."""


@dataclass(frozen=True, slots=True)
class RoutePointFormData:
    sequence: int | str | None
    city: str | None = None
    district: str | None = None
    neighborhood: str | None = None
    street: str | None = None
    avenue: str | None = None
    point_name: str | None = None
    address: str | None = None
    description: str | None = None
    latitude: float | str | None = None
    longitude: float | str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class RoutePointView:
    id: int
    service_shift_id: int
    service_id: int
    service_code: str
    shift: str
    collar: str
    sequence: int
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
    position_label: str


@dataclass(frozen=True, slots=True)
class RouteMoveResult:
    point: RoutePointView
    moved: bool


class RouteManagementService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
    ) -> None:
        self.session_factory = session_factory

    def list_points(self, service_shift_id: int) -> tuple[RoutePointView, ...]:
        try:
            with self.session_factory() as session:
                self._require_shift(session, service_shift_id)
                points = RoutePointRepository(session).list_for_shift(service_shift_id)
                return self._to_ordered_views(points)
        except RouteManagementError:
            raise
        except Exception as error:
            logger.exception("Güzergâh noktaları listelenemedi.")
            raise RouteManagementError("Güzergâh noktaları listelenemedi.") from error

    def get_point(self, point_id: int) -> RoutePointView:
        try:
            with self.session_factory() as session:
                repository = RoutePointRepository(session)
                point = repository.get_detail(point_id)
                if point is None:
                    raise RoutePointNotFoundError("Güzergâh noktası bulunamadı.")
                points = repository.list_for_shift(point.servis_vardiya_id)
                return next(
                    view for view in self._to_ordered_views(points) if view.id == point.id
                )
        except RouteManagementError:
            raise
        except Exception as error:
            logger.exception("Güzergâh noktası alınamadı: nokta_id=%s", point_id)
            raise RouteManagementError("Güzergâh noktası alınamadı.") from error

    def next_sequence(self, service_shift_id: int) -> int:
        try:
            with self.session_factory() as session:
                self._require_shift(session, service_shift_id)
                return RoutePointRepository(session).max_sequence(service_shift_id) + 1
        except RouteManagementError:
            raise
        except Exception as error:
            logger.exception("Sonraki güzergâh sıra numarası hesaplanamadı.")
            raise RouteManagementError("Sıra numarası hesaplanamadı.") from error

    def create_point(
        self,
        service_shift_id: int,
        form_data: RoutePointFormData,
    ) -> RoutePointView:
        normalized = self._normalize_form(form_data)
        try:
            with session_scope(self.session_factory) as session:
                self._require_shift(session, service_shift_id)
                repository = RoutePointRepository(session)
                sequence = normalized["sequence"]
                if sequence is None:
                    sequence = repository.max_sequence(service_shift_id) + 1
                if repository.get_by_sequence(service_shift_id, sequence) is not None:
                    raise DuplicateRouteSequenceError(
                        "Bu servis-vardiya için sıra numarası zaten kullanılıyor."
                    )
                self._ensure_not_duplicate(
                    repository.list_for_shift(service_shift_id),
                    normalized,
                )
                point = ServisGuzergahNoktasi(
                    servis_vardiya_id=service_shift_id,
                    sira_no=sequence,
                    aktif_mi=normalized["active"],
                    durak_mi=False,
                )
                self._apply_values(point, normalized)
                repository.add(point)
                RouteGeometryRepository(session).mark_stale(service_shift_id)
                stored = repository.get_detail(point.id)
                if stored is None:
                    raise RuntimeError("Oluşturulan güzergâh noktası tekrar okunamadı.")
                views = self._to_ordered_views(
                    repository.list_for_shift(service_shift_id)
                )
                result = next(view for view in views if view.id == stored.id)
            logger.info("Güzergâh noktası oluşturuldu: nokta_id=%s", result.id)
            return result
        except (
            RoutePointValidationError,
            DuplicateRouteSequenceError,
            DuplicateRoutePointError,
            RouteManagementError,
        ):
            raise
        except IntegrityError as error:
            logger.warning("Güzergâh sıra numarası çakışması oluştu.")
            raise DuplicateRouteSequenceError(
                "Bu servis-vardiya için sıra numarası zaten kullanılıyor."
            ) from error
        except Exception as error:
            logger.exception("Güzergâh noktası oluşturulamadı.")
            raise RouteManagementError(
                "Güzergâh noktası oluşturulamadı. İşlem geri alındı."
            ) from error

    def update_point(
        self,
        point_id: int,
        form_data: RoutePointFormData,
    ) -> RoutePointView:
        normalized = self._normalize_form(form_data)
        try:
            with session_scope(self.session_factory) as session:
                repository = RoutePointRepository(session)
                point = repository.get_detail(point_id)
                if point is None:
                    raise RoutePointNotFoundError("Güncellenecek güzergâh noktası bulunamadı.")
                sequence = normalized["sequence"] or point.sira_no
                sequence_owner = repository.get_by_sequence(
                    point.servis_vardiya_id,
                    sequence,
                )
                if sequence_owner is not None and sequence_owner.id != point.id:
                    raise DuplicateRouteSequenceError(
                        "Bu servis-vardiya için sıra numarası zaten kullanılıyor."
                    )
                self._ensure_not_duplicate(
                    repository.list_for_shift(point.servis_vardiya_id),
                    normalized,
                    excluded_point_id=point.id,
                )
                point.sira_no = sequence
                point.aktif_mi = normalized["active"]
                self._apply_values(point, normalized)
                RouteGeometryRepository(session).mark_stale(
                    point.servis_vardiya_id
                )
                repository.session.flush()
                views = self._to_ordered_views(
                    repository.list_for_shift(point.servis_vardiya_id)
                )
                result = next(view for view in views if view.id == point.id)
            logger.info("Güzergâh noktası güncellendi: nokta_id=%s", point_id)
            return result
        except (
            RoutePointNotFoundError,
            RoutePointValidationError,
            DuplicateRouteSequenceError,
            DuplicateRoutePointError,
        ):
            raise
        except IntegrityError as error:
            logger.warning("Güzergâh noktası güncellemesinde sıra çakışması oluştu.")
            raise DuplicateRouteSequenceError(
                "Bu servis-vardiya için sıra numarası zaten kullanılıyor."
            ) from error
        except Exception as error:
            logger.exception("Güzergâh noktası güncellenemedi: nokta_id=%s", point_id)
            raise RouteManagementError(
                "Güzergâh noktası güncellenemedi. İşlem geri alındı."
            ) from error

    def set_point_active(self, point_id: int, active: bool) -> RoutePointView:
        current = self.get_point(point_id)
        return self.update_point(
            point_id,
            RoutePointFormData(
                sequence=current.sequence,
                city=current.city,
                district=current.district,
                neighborhood=current.neighborhood,
                street=current.street,
                avenue=current.avenue,
                point_name=current.point_name,
                address=current.address,
                description=current.description,
                latitude=current.latitude,
                longitude=current.longitude,
                active=active,
            ),
        )

    def move_point(self, point_id: int, direction: int) -> RouteMoveResult:
        if direction not in {-1, 1}:
            raise RoutePointValidationError("Taşıma yönü geçersiz.")
        try:
            with session_scope(self.session_factory) as session:
                repository = RoutePointRepository(session)
                point = repository.get_detail(point_id)
                if point is None:
                    raise RoutePointNotFoundError("Taşınacak güzergâh noktası bulunamadı.")
                points = repository.list_for_shift(point.servis_vardiya_id)
                current_index = next(
                    index for index, item in enumerate(points) if item.id == point.id
                )
                target_index = current_index + direction
                moved = 0 <= target_index < len(points)
                if moved:
                    points[current_index], points[target_index] = (
                        points[target_index],
                        points[current_index],
                    )
                    repository.resequence(points)
                    RouteGeometryRepository(session).mark_stale(
                        point.servis_vardiya_id
                    )
                views = self._to_ordered_views(
                    repository.list_for_shift(point.servis_vardiya_id)
                )
                result = next(view for view in views if view.id == point.id)
            logger.info(
                "Güzergâh noktası taşındı: nokta_id=%s, yon=%s, degisti=%s",
                point_id,
                direction,
                moved,
            )
            return RouteMoveResult(point=result, moved=moved)
        except (RoutePointNotFoundError, RoutePointValidationError):
            raise
        except Exception as error:
            logger.exception("Güzergâh noktası taşınamadı: nokta_id=%s", point_id)
            raise RouteManagementError(
                "Güzergâh noktası taşınamadı. İşlem geri alındı."
            ) from error

    @staticmethod
    def _require_shift(session: Session, service_shift_id: int):
        shift = ServisVardiyasiRepository(session).get_detail(service_shift_id)
        if shift is None:
            raise RouteManagementError("Seçilen servis-vardiya kaydı bulunamadı.")
        return shift

    @classmethod
    def _normalize_form(cls, form_data: RoutePointFormData) -> dict[str, object]:
        sequence = None
        if not is_blank(form_data.sequence):
            try:
                sequence = normalize_positive_integer(form_data.sequence, "Sıra numarası")
            except ValueNormalizationError as error:
                raise RoutePointValidationError(error.message) from error

        values = {
            "city": cls._optional_text(form_data.city, "İl"),
            "district": cls._optional_text(form_data.district, "İlçe"),
            "neighborhood": cls._optional_text(form_data.neighborhood, "Mahalle"),
            "street": cls._optional_text(form_data.street, "Sokak"),
            "avenue": cls._optional_text(form_data.avenue, "Cadde"),
            "point_name": cls._optional_text(form_data.point_name, "Nokta adı"),
            "address": cls._optional_text(form_data.address, "Açık adres"),
            "description": cls._optional_text(form_data.description, "Açıklama"),
        }
        extracted_roads = extract_address_roads(values["address"])
        values["street"] = values["street"] or extracted_roads.street
        values["avenue"] = values["avenue"] or extracted_roads.avenue
        if not any(
            values[field] is not None
            for field in ("district", "neighborhood", "point_name")
        ):
            raise RoutePointValidationError(
                "İlçe, mahalle veya nokta adı alanlarından en az biri dolu olmalıdır."
            )
        try:
            latitude = normalize_coordinate(form_data.latitude, latitude=True)
            longitude = normalize_coordinate(form_data.longitude, latitude=False)
        except ValueNormalizationError as error:
            raise RoutePointValidationError(error.message) from error
        if (latitude is None) != (longitude is None):
            raise RoutePointValidationError(
                "Enlem ve boylam birlikte doldurulmalı veya ikisi de boş bırakılmalıdır."
            )
        if not isinstance(form_data.active, bool):
            raise RoutePointValidationError("Aktiflik değeri geçersiz.")
        return {
            "sequence": sequence,
            **values,
            "latitude": latitude,
            "longitude": longitude,
            "active": form_data.active,
        }

    @staticmethod
    def _optional_text(value: object, label: str) -> str | None:
        normalized = normalize_text(value)
        if normalized is not None and is_excel_error(normalized):
            raise RoutePointValidationError(f"{label} Excel hata değeri içeremez.")
        return normalized

    @classmethod
    def _ensure_not_duplicate(
        cls,
        existing_points: list[ServisGuzergahNoktasi],
        normalized: dict[str, object],
        *,
        excluded_point_id: int | None = None,
    ) -> None:
        target_key = cls._normalized_key(
            normalized["city"],
            normalized["district"],
            normalized["neighborhood"],
            normalized["street"],
            normalized["avenue"],
            normalized["point_name"],
        )
        for point in existing_points:
            if point.id == excluded_point_id:
                continue
            if cls._normalized_key(
                point.il,
                point.ilce,
                point.mahalle,
                point.sokak,
                point.cadde,
                point.nokta_adi,
            ) == target_key:
                raise DuplicateRoutePointError(
                    "Aynı güzergâh noktası bu servis-vardiya için zaten kayıtlı."
                )

    @staticmethod
    def _normalized_key(*values: object) -> tuple[str, ...]:
        return tuple(comparison_key(value) or "" for value in values)

    @staticmethod
    def _apply_values(
        point: ServisGuzergahNoktasi,
        normalized: dict[str, object],
    ) -> None:
        previous_address = AddressInput(
            address=point.acik_adres,
            city=point.il,
            district=point.ilce,
            neighborhood=point.mahalle,
            street=point.sokak,
            avenue=point.cadde,
        ).signature()
        previous_coordinates = (point.enlem, point.boylam)
        point.il = normalized["city"]
        point.ilce = normalized["district"]
        point.mahalle = normalized["neighborhood"]
        point.sokak = normalized["street"]
        point.cadde = normalized["avenue"]
        point.nokta_adi = normalized["point_name"]
        point.acik_adres = normalized["address"]
        point.aciklama = normalized["description"]
        point.enlem = normalized["latitude"]
        point.boylam = normalized["longitude"]
        current_address = AddressInput(
            address=point.acik_adres,
            city=point.il,
            district=point.ilce,
            neighborhood=point.mahalle,
            street=point.sokak,
            avenue=point.cadde,
        )
        if point.enlem is None or point.boylam is None:
            point.koordinat_durumu = "EKSIK"
            point.koordinat_adres_ozeti = None
        elif previous_coordinates != (point.enlem, point.boylam):
            point.koordinat_durumu = "MANUEL"
            point.koordinat_adres_ozeti = current_address.signature()
        elif previous_address != current_address.signature():
            point.koordinat_durumu = "GUNCEL_DEGIL"

    @classmethod
    def _to_ordered_views(
        cls,
        points: list[ServisGuzergahNoktasi],
    ) -> tuple[RoutePointView, ...]:
        count = len(points)
        return tuple(
            cls._to_view(point, index=index, count=count)
            for index, point in enumerate(points)
        )

    @staticmethod
    def _to_view(
        point: ServisGuzergahNoktasi,
        *,
        index: int,
        count: int,
    ) -> RoutePointView:
        if count == 1:
            position_label = "Başlangıç / Bitiş"
        elif index == 0:
            position_label = "Başlangıç"
        elif index == count - 1:
            position_label = "Bitiş"
        else:
            position_label = "Ara Nokta"
        shift = point.servis_vardiyasi
        return RoutePointView(
            id=point.id,
            service_shift_id=point.servis_vardiya_id,
            service_id=shift.servis_id,
            service_code=shift.servis.servis_kodu,
            shift=shift.vardiya_turu.value,
            collar=shift.yaka_turu.value,
            sequence=point.sira_no,
            city=point.il,
            district=point.ilce,
            neighborhood=point.mahalle,
            street=point.sokak,
            avenue=point.cadde,
            point_name=point.nokta_adi,
            address=point.acik_adres,
            description=point.aciklama,
            latitude=point.enlem,
            longitude=point.boylam,
            active=point.aktif_mi,
            position_label=position_label,
        )
