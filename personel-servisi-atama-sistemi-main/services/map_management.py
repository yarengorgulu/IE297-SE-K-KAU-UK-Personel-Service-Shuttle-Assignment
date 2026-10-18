"""Koordinat onayı, toplu çözümleme ve servis-vardiya rota yönetimi."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from sqlalchemy.orm import Session, sessionmaker

from app.factory import (
    DEFAULT_FACTORY_DESTINATION,
    FACTORY_DESTINATION_SETTING_KEY,
    FactoryDestination,
)
from database.models import (
    AdresOnbellegi,
    Personel,
    ServisGuzergahGeometrisi,
    ServisGuzergahNoktasi,
)
from database.repositories import (
    AddressCacheRepository,
    ApplicationSettingRepository,
    PersonnelCoordinateRepository,
    RouteGeometryRepository,
    RoutePointCoordinateRepository,
    RoutePointRepository,
    ServisVardiyasiRepository,
)
from database.session import SessionFactory, session_scope
from normalization.text import comparison_key
from normalization.values import ValueNormalizationError, normalize_coordinate
from services.external_http import ExternalServiceError
from services.geocoding import (
    AddressInput,
    AddressMismatch,
    GeocodingCandidate,
    NominatimGeocodingService,
    assess_candidate,
)
from services.routing import OsrmRoutingService, RouteCoordinate


logger = logging.getLogger(__name__)

CoordinateTargetType = Literal["PERSONEL", "GUZERGAH"]


class MapManagementError(RuntimeError):
    """Harita ve koordinat işlemleri için kullanıcıya güvenli temel hata."""


class CoordinateTargetNotFoundError(MapManagementError):
    """Personel veya güzergâh noktası bulunamadığında üretilir."""


class CoordinateValidationError(MapManagementError):
    """Manuel koordinat geçersiz olduğunda üretilir."""


class RouteCalculationError(MapManagementError):
    """Servis-vardiya rotası hesaplanamadığında üretilir."""


@dataclass(frozen=True, slots=True)
class CoordinateTargetView:
    target_type: CoordinateTargetType
    id: int
    label: str
    address: str | None
    city: str | None
    district: str | None
    neighborhood: str | None
    street: str | None
    avenue: str | None
    latitude: float | None
    longitude: float | None
    coordinate_status: str

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def address_input(self) -> AddressInput:
        return AddressInput(
            address=self.address,
            city=self.city,
            district=self.district,
            neighborhood=self.neighborhood,
            street=self.street,
            avenue=self.avenue,
        )


@dataclass(frozen=True, slots=True)
class CandidateReview:
    candidate: GeocodingCandidate
    mismatches: tuple[AddressMismatch, ...]
    score: int = 0
    status: str = "EK KONTROL GEREKLİ"
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GeocodingLookupResult:
    target: CoordinateTargetView
    candidates: tuple[CandidateReview, ...]
    skipped_existing: bool = False
    used_cache: bool = False
    used_fallback: bool = False


@dataclass(frozen=True, slots=True)
class BulkGeocodingResult:
    total_count: int
    success_count: int
    not_found_count: int
    warning_count: int
    failure_count: int
    cancelled: bool


@dataclass(frozen=True, slots=True)
class RoutePointMapView:
    id: int | None
    sequence: int
    label: str
    district: str | None
    neighborhood: str | None
    latitude: float | None
    longitude: float | None
    active: bool
    position_label: str
    is_destination: bool = False


@dataclass(frozen=True, slots=True)
class RouteMapData:
    service_shift_id: int
    service_code: str
    shift: str
    collar: str
    points: tuple[RoutePointMapView, ...]
    geometry: dict[str, object] | None
    distance_meters: float | None
    duration_seconds: float | None
    route_is_current: bool


@dataclass(frozen=True, slots=True)
class RouteCalculationResult:
    map_data: RouteMapData
    distance_meters: float
    duration_seconds: float


class CoordinateManagementService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
        *,
        geocoder: NominatimGeocodingService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.geocoder = geocoder or NominatimGeocodingService()

    def get_personnel_target(self, personnel_id: int) -> CoordinateTargetView:
        with self.session_factory() as session:
            personnel = PersonnelCoordinateRepository(session).get_by_id(personnel_id)
            if personnel is None:
                raise CoordinateTargetNotFoundError("Personel kaydı bulunamadı.")
            return self._personnel_view(personnel)

    def get_route_target(self, point_id: int) -> CoordinateTargetView:
        with self.session_factory() as session:
            point = RoutePointCoordinateRepository(session).get_detail(point_id)
            if point is None:
                raise CoordinateTargetNotFoundError("Güzergâh noktası bulunamadı.")
            return self._route_view(point)

    def search_personnel(
        self,
        personnel_id: int,
        *,
        force: bool = False,
    ) -> GeocodingLookupResult:
        return self._search_target("PERSONEL", personnel_id, force=force)

    def search_route_point(
        self,
        point_id: int,
        *,
        force: bool = False,
    ) -> GeocodingLookupResult:
        return self._search_target("GUZERGAH", point_id, force=force)

    def _search_target(
        self,
        target_type: CoordinateTargetType,
        target_id: int,
        *,
        force: bool,
    ) -> GeocodingLookupResult:
        target = (
            self.get_personnel_target(target_id)
            if target_type == "PERSONEL"
            else self.get_route_target(target_id)
        )
        if (
            target.has_coordinates
            and target.coordinate_status != "GUNCEL_DEGIL"
            and not force
        ):
            return GeocodingLookupResult(
                target=target,
                candidates=(),
                skipped_existing=True,
            )
        address = target.address_input()
        cache_key = self._cache_key(address)
        with self.session_factory() as session:
            cached = AddressCacheRepository(session).get_by_normalized_address(cache_key)
            if cached is not None and not force:
                candidate = GeocodingCandidate(
                    place_id=f"cache-{cached.id}",
                    display_name="Daha önce onaylanmış yerel adres sonucu",
                    latitude=cached.enlem,
                    longitude=cached.boylam,
                    city=target.city,
                    district=target.district,
                    neighborhood=target.neighborhood,
                    street=target.street,
                    avenue=target.avenue,
                )
                return GeocodingLookupResult(
                    target=target,
                    candidates=(
                        CandidateReview(
                            candidate,
                            (),
                            score=100,
                            status="YEREL ONAYLI SONUÇ",
                            details=("Daha önce kullanıcı tarafından onaylandı",),
                        ),
                    ),
                    used_cache=True,
                )
        try:
            response = self.geocoder.search(address)
        except ExternalServiceError as error:
            raise MapManagementError(str(error)) from error
        if not response.candidates:
            self._mark_not_found(target_type, target_id, address.signature())
            return GeocodingLookupResult(
                target=target,
                candidates=(),
                used_fallback=response.used_fallback,
            )
        reviews: list[CandidateReview] = []
        for candidate in response.candidates:
            assessment = assess_candidate(address, candidate)
            reviews.append(
                CandidateReview(
                    candidate=candidate,
                    mismatches=assessment.mismatches,
                    score=assessment.score,
                    status=assessment.status,
                    details=assessment.details,
                )
            )
        return GeocodingLookupResult(
            target=target,
            candidates=tuple(reviews),
            used_fallback=response.used_fallback,
        )

    def confirm_personnel_candidate(
        self,
        personnel_id: int,
        candidate: GeocodingCandidate,
        *,
        accept_found_address: bool = False,
    ) -> CoordinateTargetView:
        return self._confirm_candidate(
            "PERSONEL",
            personnel_id,
            candidate,
            accept_found_address=accept_found_address,
        )

    def confirm_route_candidate(
        self,
        point_id: int,
        candidate: GeocodingCandidate,
        *,
        accept_found_address: bool = False,
    ) -> CoordinateTargetView:
        return self._confirm_candidate(
            "GUZERGAH",
            point_id,
            candidate,
            accept_found_address=accept_found_address,
        )

    def _confirm_candidate(
        self,
        target_type: CoordinateTargetType,
        target_id: int,
        candidate: GeocodingCandidate,
        *,
        accept_found_address: bool,
    ) -> CoordinateTargetView:
        latitude, longitude = self._validated_coordinates(
            candidate.latitude,
            candidate.longitude,
        )
        with session_scope(self.session_factory) as session:
            target = self._get_model(session, target_type, target_id)
            if accept_found_address:
                if candidate.city:
                    target.il = candidate.city
                if candidate.district:
                    target.ilce = candidate.district
                if candidate.neighborhood:
                    target.mahalle = candidate.neighborhood
                if candidate.street:
                    target.sokak = candidate.street
                if candidate.avenue:
                    target.cadde = candidate.avenue
            address = self._model_address(target)
            target.enlem = latitude
            target.boylam = longitude
            target.koordinat_durumu = "COZUMLENDI"
            target.koordinat_adres_ozeti = address.signature()
            if target_type == "GUZERGAH":
                RouteGeometryRepository(session).mark_stale(target.servis_vardiya_id)
            cache_repository = AddressCacheRepository(session)
            cache_key = self._cache_key(address)
            if cache_repository.get_by_normalized_address(cache_key) is None:
                cache_repository.add(
                    AdresOnbellegi(
                        normalize_adres=cache_key,
                        orijinal_adres=address.full_query(),
                        enlem=latitude,
                        boylam=longitude,
                        kaynak="NOMINATIM_ONAYLI",
                    )
                )
            view = self._model_view(target_type, target)
        return view

    def set_personnel_coordinates(
        self,
        personnel_id: int,
        latitude: object,
        longitude: object,
    ) -> CoordinateTargetView:
        return self._set_manual("PERSONEL", personnel_id, latitude, longitude)

    def set_route_coordinates(
        self,
        point_id: int,
        latitude: object,
        longitude: object,
    ) -> CoordinateTargetView:
        return self._set_manual("GUZERGAH", point_id, latitude, longitude)

    def _set_manual(
        self,
        target_type: CoordinateTargetType,
        target_id: int,
        latitude: object,
        longitude: object,
    ) -> CoordinateTargetView:
        resolved_latitude, resolved_longitude = self._validated_coordinates(
            latitude,
            longitude,
        )
        with session_scope(self.session_factory) as session:
            target = self._get_model(session, target_type, target_id)
            target.enlem = resolved_latitude
            target.boylam = resolved_longitude
            target.koordinat_durumu = "MANUEL"
            target.koordinat_adres_ozeti = self._model_address(target).signature()
            if target_type == "GUZERGAH":
                RouteGeometryRepository(session).mark_stale(target.servis_vardiya_id)
            view = self._model_view(target_type, target)
        return view

    def bulk_geocode_personnel(
        self,
        progress: Callable[[int, int], None],
        cancelled: Callable[[], bool],
    ) -> BulkGeocodingResult:
        with self.session_factory() as session:
            ids = [
                item.id
                for item in PersonnelCoordinateRepository(session).list_missing_or_stale()
            ]
        return self._bulk("PERSONEL", ids, progress, cancelled)

    def bulk_geocode_route_points(
        self,
        progress: Callable[[int, int], None],
        cancelled: Callable[[], bool],
    ) -> BulkGeocodingResult:
        with self.session_factory() as session:
            ids = [
                item.id
                for item in RoutePointCoordinateRepository(session).list_missing_or_stale()
            ]
        return self._bulk("GUZERGAH", ids, progress, cancelled)

    def _bulk(
        self,
        target_type: CoordinateTargetType,
        target_ids: list[int],
        progress: Callable[[int, int], None],
        cancelled: Callable[[], bool],
    ) -> BulkGeocodingResult:
        success = not_found = warnings = failures = 0
        total = len(target_ids)
        was_cancelled = False
        for index, target_id in enumerate(target_ids, start=1):
            if cancelled():
                was_cancelled = True
                break
            try:
                lookup = self._search_target(target_type, target_id, force=False)
                if not lookup.candidates:
                    not_found += 1
                elif len(lookup.candidates) == 1 and not lookup.candidates[0].mismatches:
                    review = lookup.candidates[0]
                    if target_type == "PERSONEL":
                        self.confirm_personnel_candidate(target_id, review.candidate)
                    else:
                        self.confirm_route_candidate(target_id, review.candidate)
                    success += 1
                else:
                    warnings += 1
            except Exception:
                failures += 1
                logger.exception(
                    "Toplu koordinat çözümlemede bir kayıt başarısız oldu: tür=%s, id=%s",
                    target_type,
                    target_id,
                )
            progress(index, total)
        return BulkGeocodingResult(
            total_count=total,
            success_count=success,
            not_found_count=not_found,
            warning_count=warnings,
            failure_count=failures,
            cancelled=was_cancelled,
        )

    def _mark_not_found(
        self,
        target_type: CoordinateTargetType,
        target_id: int,
        address_signature: str,
    ) -> None:
        with session_scope(self.session_factory) as session:
            target = self._get_model(session, target_type, target_id)
            target.koordinat_durumu = "BULUNAMADI"
            target.koordinat_adres_ozeti = address_signature

    @staticmethod
    def _validated_coordinates(latitude: object, longitude: object) -> tuple[float, float]:
        try:
            resolved_latitude = normalize_coordinate(latitude, latitude=True)
            resolved_longitude = normalize_coordinate(longitude, latitude=False)
        except ValueNormalizationError as error:
            raise CoordinateValidationError(error.message) from error
        if resolved_latitude is None or resolved_longitude is None:
            raise CoordinateValidationError("Enlem ve boylam birlikte doldurulmalıdır.")
        return resolved_latitude, resolved_longitude

    @staticmethod
    def _cache_key(address: AddressInput) -> str:
        return comparison_key(address.full_query()) or address.signature()

    @staticmethod
    def _get_model(
        session: Session,
        target_type: CoordinateTargetType,
        target_id: int,
    ) -> Personel | ServisGuzergahNoktasi:
        target = (
            PersonnelCoordinateRepository(session).get_by_id(target_id)
            if target_type == "PERSONEL"
            else RoutePointCoordinateRepository(session).get_detail(target_id)
        )
        if target is None:
            raise CoordinateTargetNotFoundError("Koordinat kaydı bulunamadı.")
        return target

    @staticmethod
    def _model_address(target: Personel | ServisGuzergahNoktasi) -> AddressInput:
        return AddressInput(
            address=target.acik_adres,
            city=target.il,
            district=target.ilce,
            neighborhood=target.mahalle,
            street=target.sokak,
            avenue=target.cadde,
        )

    @classmethod
    def _model_view(
        cls,
        target_type: CoordinateTargetType,
        target: Personel | ServisGuzergahNoktasi,
    ) -> CoordinateTargetView:
        return (
            cls._personnel_view(target)
            if target_type == "PERSONEL"
            else cls._route_view(target)
        )

    @staticmethod
    def _personnel_view(personnel: Personel) -> CoordinateTargetView:
        return CoordinateTargetView(
            target_type="PERSONEL",
            id=personnel.id,
            label=f"{personnel.sicil_no} - {personnel.ad_soyad or 'Adsız personel'}",
            address=personnel.acik_adres,
            city=personnel.il,
            district=personnel.ilce,
            neighborhood=personnel.mahalle,
            street=personnel.sokak,
            avenue=personnel.cadde,
            latitude=personnel.enlem,
            longitude=personnel.boylam,
            coordinate_status=personnel.koordinat_durumu,
        )

    @staticmethod
    def _route_view(point: ServisGuzergahNoktasi) -> CoordinateTargetView:
        return CoordinateTargetView(
            target_type="GUZERGAH",
            id=point.id,
            label=f"Sıra {point.sira_no} - {point.nokta_adi or point.mahalle or point.ilce}",
            address=point.acik_adres,
            city=point.il,
            district=point.ilce,
            neighborhood=point.mahalle,
            street=point.sokak,
            avenue=point.cadde,
            latitude=point.enlem,
            longitude=point.boylam,
            coordinate_status=point.koordinat_durumu,
        )


class RouteCalculationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
        *,
        router: OsrmRoutingService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.router = router or OsrmRoutingService()

    def get_factory_destination(self) -> FactoryDestination:
        with self.session_factory() as session:
            return self._factory_destination(session)

    def set_factory_coordinates(
        self,
        latitude: object,
        longitude: object,
    ) -> FactoryDestination:
        try:
            resolved_latitude = normalize_coordinate(latitude, latitude=True)
            resolved_longitude = normalize_coordinate(longitude, latitude=False)
        except ValueNormalizationError as error:
            raise CoordinateValidationError(str(error)) from error
        if resolved_latitude is None or resolved_longitude is None:
            raise CoordinateValidationError("Fabrika enlem ve boylamı zorunludur.")
        with session_scope(self.session_factory) as session:
            current = self._factory_destination(session)
            updated = current.with_coordinates(
                resolved_latitude,
                resolved_longitude,
            )
            ApplicationSettingRepository(session).set_value(
                FACTORY_DESTINATION_SETTING_KEY,
                updated.to_json(),
            )
            RouteGeometryRepository(session).mark_all_stale()
        return updated

    def calculate_shift(self, shift_id: int) -> RouteCalculationResult:
        with self.session_factory() as session:
            shift = ServisVardiyasiRepository(session).get_detail(shift_id)
            if shift is None:
                raise RouteCalculationError("Servis-vardiya kaydı bulunamadı.")
            points = RoutePointRepository(session).list_for_shift(
                shift_id,
                include_inactive=False,
            )
            destination = self._factory_destination(session)
            signature = self.point_signature(points, destination)
            coordinates = self._coordinates(points)
        try:
            optimized = self.router.calculate_optimized_to_destination(
                coordinates,
                RouteCoordinate(
                    latitude=destination.latitude,
                    longitude=destination.longitude,
                ),
            )
        except ExternalServiceError as error:
            raise RouteCalculationError(str(error)) from error
        expected_indexes = tuple(range(len(points)))
        if tuple(sorted(optimized.pickup_order)) != expected_indexes:
            raise RouteCalculationError("Rota servisi geçersiz bir toplama sırası döndürdü.")
        ordered_ids = [points[index].id for index in optimized.pickup_order]
        calculated = optimized.route

        with session_scope(self.session_factory) as session:
            point_repository = RoutePointRepository(session)
            all_points = point_repository.list_for_shift(
                shift_id,
                include_inactive=True,
            )
            current_points = [point for point in all_points if point.aktif_mi]
            current_destination = self._factory_destination(session)
            if current_destination != destination:
                raise RouteCalculationError(
                    "Fabrika varış noktası hesaplama sırasında değişti. Lütfen yeniden deneyin."
                )
            if self.point_signature(current_points, destination) != signature:
                raise RouteCalculationError(
                    "Güzergâh noktaları hesaplama sırasında değişti. Lütfen yeniden deneyin."
                )
            active_by_id = {point.id: point for point in current_points}
            try:
                optimized_points = [active_by_id[point_id] for point_id in ordered_ids]
            except KeyError as error:
                raise RouteCalculationError(
                    "Optimize edilen güzergâh noktalarından biri artık bulunmuyor."
                ) from error
            inactive_points = [point for point in all_points if not point.aktif_mi]
            point_repository.resequence(optimized_points + inactive_points)
            new_signature = self.point_signature(optimized_points, destination)
            repository = RouteGeometryRepository(session)
            geometry = repository.get_for_shift(shift_id)
            if geometry is None:
                geometry = repository.add(
                    ServisGuzergahGeometrisi(
                        servis_vardiya_id=shift_id,
                        geojson="{}",
                        toplam_mesafe_metre=0,
                        toplam_sure_saniye=0,
                        nokta_ozeti=new_signature,
                    )
                )
            geometry.geojson = json.dumps(calculated.geometry, ensure_ascii=False)
            geometry.toplam_mesafe_metre = calculated.distance_meters
            geometry.toplam_sure_saniye = calculated.duration_seconds
            geometry.nokta_ozeti = new_signature
            geometry.guncel_mi = True
            geometry.aktif_mi = True
            geometry.hesaplanma_tarihi = datetime.now()
        map_data = self.get_shift_map_data(shift_id)
        return RouteCalculationResult(
            map_data=map_data,
            distance_meters=calculated.distance_meters,
            duration_seconds=calculated.duration_seconds,
        )

    def get_shift_map_data(self, shift_id: int) -> RouteMapData:
        with self.session_factory() as session:
            shift = ServisVardiyasiRepository(session).get_detail(shift_id)
            if shift is None:
                raise RouteCalculationError("Servis-vardiya kaydı bulunamadı.")
            points = RoutePointRepository(session).list_for_shift(shift_id)
            active_points = [point for point in points if point.aktif_mi]
            destination = self._factory_destination(session)
            signature = self.point_signature(active_points, destination)
            stored = RouteGeometryRepository(session).get_for_shift(shift_id)
            geometry = None
            distance = duration = None
            is_current = False
            if stored is not None:
                is_current = stored.guncel_mi and stored.nokta_ozeti == signature
                distance = stored.toplam_mesafe_metre
                duration = stored.toplam_sure_saniye
                try:
                    decoded = json.loads(stored.geojson)
                    if isinstance(decoded, dict):
                        geometry = decoded
                except json.JSONDecodeError:
                    logger.warning("Saklanan rota geometrisi okunamadı: vardiya_id=%s", shift_id)
            point_views = self._point_views(active_points, destination)
            return RouteMapData(
                service_shift_id=shift.id,
                service_code=shift.servis.servis_kodu,
                shift=shift.vardiya_turu.value,
                collar=shift.yaka_turu.value,
                points=point_views,
                geometry=geometry,
                distance_meters=distance,
                duration_seconds=duration,
                route_is_current=is_current,
            )

    @staticmethod
    def _coordinates(
        points: list[ServisGuzergahNoktasi],
    ) -> tuple[RouteCoordinate, ...]:
        if not points:
            raise RouteCalculationError(
                "Rota hesaplamak için en az bir aktif toplama noktası gerekir."
            )
        missing = [point.sira_no for point in points if point.enlem is None or point.boylam is None]
        if missing:
            raise RouteCalculationError(
                "Rota hesaplanmadan önce şu sıra numaralarının koordinatları tamamlanmalıdır: "
                + ", ".join(str(value) for value in missing)
            )
        stale = [
            point.sira_no
            for point in points
            if point.koordinat_durumu in {"BULUNAMADI", "GUNCEL_DEGIL"}
        ]
        if stale:
            raise RouteCalculationError(
                "Rota hesaplanmadan önce şu sıra numaralarının güncel olmayan "
                "koordinatları yeniden çözülmelidir: "
                + ", ".join(str(value) for value in stale)
            )
        return tuple(
            RouteCoordinate(latitude=float(point.enlem), longitude=float(point.boylam))
            for point in points
        )

    @staticmethod
    def point_signature(
        points: list[ServisGuzergahNoktasi],
        destination: FactoryDestination | None = None,
    ) -> str:
        content = "|".join(
            f"{point.id}:{point.sira_no}:{point.enlem}:{point.boylam}:"
            f"{point.koordinat_durumu}:{int(point.aktif_mi)}"
            for point in points
        )
        if destination is not None:
            content += (
                f"|FABRIKA:{destination.name}:{destination.latitude}:"
                f"{destination.longitude}"
            )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _point_views(
        points: list[ServisGuzergahNoktasi],
        destination: FactoryDestination,
    ) -> tuple[RoutePointMapView, ...]:
        views: list[RoutePointMapView] = []
        for index, point in enumerate(points):
            if index == 0:
                position = "Başlangıç"
            else:
                position = "Ara Nokta"
            views.append(
                RoutePointMapView(
                    id=point.id,
                    sequence=point.sira_no,
                    label=point.nokta_adi or point.mahalle or point.ilce or "İsimsiz nokta",
                    district=point.ilce,
                    neighborhood=point.mahalle,
                    latitude=point.enlem,
                    longitude=point.boylam,
                    active=point.aktif_mi,
                    position_label=position,
                )
            )
        views.append(
            RoutePointMapView(
                id=None,
                sequence=len(points) + 1,
                label=destination.name,
                district=destination.district,
                neighborhood=destination.neighborhood,
                latitude=destination.latitude,
                longitude=destination.longitude,
                active=True,
                position_label="Bitiş / Fabrika",
                is_destination=True,
            )
        )
        return tuple(views)

    @staticmethod
    def _factory_destination(session: Session) -> FactoryDestination:
        value = ApplicationSettingRepository(session).get_value(
            FACTORY_DESTINATION_SETTING_KEY
        )
        if value is None:
            return DEFAULT_FACTORY_DESTINATION
        try:
            return FactoryDestination.from_json(value)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RouteCalculationError(
                "Fabrika varış noktası ayarı geçersiz. Koordinatları yeniden kaydedin."
            ) from error
