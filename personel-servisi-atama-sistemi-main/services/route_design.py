"""Aşama 11 rota tasarımı, kayıt ve kontrollü silme iş kuralları."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.factory import DEFAULT_FACTORY_DESTINATION, FactoryDestination
from database.models import Rota, RotaKontrolNoktasi, Servis, ServisVardiyasi
from database.repositories import (
    ApplicationSettingRepository,
    SavedRouteRepository,
    ServisRepository,
    ServisVardiyasiRepository,
)
from database.session import SessionFactory, session_scope
from normalization.text import normalize_text
from normalization.values import (
    ValueNormalizationError,
    normalize_collar,
    normalize_coordinate,
    normalize_positive_integer,
    normalize_service_code,
    normalize_shift,
)
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


class RouteDesignError(RuntimeError):
    """Kullanıcıya gösterilebilen rota tasarımı hatası."""


class RouteDesignValidationError(RouteDesignError):
    pass


class RouteNotFoundError(RouteDesignError):
    pass


class AddressConfirmationRequired(RouteDesignError):
    def __init__(
        self,
        point_index: int,
        candidate: GeocodingCandidate,
        mismatches: tuple[AddressMismatch, ...],
    ) -> None:
        self.point_index = point_index
        self.candidate = candidate
        self.mismatches = mismatches
        details = "; ".join(item.message for item in mismatches)
        super().__init__(
            f"{point_index}. adres için kullanıcı onayı gerekiyor: {details}"
        )


@dataclass(frozen=True, slots=True)
class RouteDesignPoint:
    name: str | None = None
    address: str | None = None
    neighborhood: str | None = None
    street: str | None = None
    avenue: str | None = None
    district: str | None = None
    city: str | None = None
    latitude: float | str | None = None
    longitude: float | str | None = None
    coordinate_status: str = "MANUEL"
    active: bool = True

    def address_input(self) -> AddressInput:
        return AddressInput(
            self.address,
            self.city,
            self.district,
            self.neighborhood,
            self.street,
            self.avenue,
        )


@dataclass(frozen=True, slots=True)
class RouteCalculation:
    points: tuple[RouteDesignPoint, ...]
    destination: FactoryDestination
    distance_meters: float
    duration_seconds: float
    geometry: dict[str, object]


@dataclass(frozen=True, slots=True)
class NewServiceForRoute:
    service_code: str
    shift: str
    collar: str
    capacity: int | str
    active: bool = True
    service_code_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class RouteSaveRequest:
    route_name: str
    creation_method: str
    calculation: RouteCalculation
    shift_ids: tuple[int, ...] = ()
    active: bool = True
    new_service: NewServiceForRoute | None = None


@dataclass(frozen=True, slots=True)
class SavedRouteView:
    id: int
    route_name: str
    creation_method: str
    active: bool
    start_name: str
    destination_name: str
    distance_meters: float
    duration_seconds: float
    geometry: dict[str, object]
    points: tuple[RouteDesignPoint, ...]
    shift_ids: tuple[int, ...]
    shift_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RouteSummary:
    route_name: str
    service_shifts: tuple[str, ...]
    capacity: int
    distance_km: float
    duration_minutes: float
    point_count: int
    creation_method: str


@dataclass(frozen=True, slots=True)
class ControlledDeleteResult:
    physical_deleted: bool
    deactivated: bool
    message: str


class RouteDesignService:
    """Arayüzden bağımsız ve transaction sınırları belirli rota servisi."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
        *,
        geocoder: NominatimGeocodingService | None = None,
        router: OsrmRoutingService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.geocoder = geocoder or NominatimGeocodingService()
        self.router = router or OsrmRoutingService()

    def factory_destination(self) -> FactoryDestination:
        with self.session_factory() as session:
            value = ApplicationSettingRepository(session).get_value(
                "fabrika_varis_noktasi"
            )
        if value:
            try:
                return FactoryDestination.from_json(value)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.exception("Fabrika varış ayarı okunamadı; varsayılan kullanıldı.")
        return DEFAULT_FACTORY_DESTINATION

    def calculate_manual(
        self,
        points: tuple[RouteDesignPoint, ...],
    ) -> RouteCalculation:
        normalized = self._coordinate_points(points)
        return self._calculate(normalized)

    def calculate_drawn_trace(
        self,
        points: tuple[RouteDesignPoint, ...],
    ) -> RouteCalculation:
        """Çizimi önce sürülebilir yola oturtur, ardından fabrikada bitirir."""
        normalized = self._coordinate_points(points)
        if len(normalized) < 2:
            raise RouteDesignValidationError(
                "Çizerek rota oluşturmak için en az iki farklı nokta gerekir."
            )
        coordinates = tuple(
            RouteCoordinate(float(point.latitude), float(point.longitude))
            for point in normalized
        )
        try:
            matched = self.router.match_trace(coordinates, radius_meters=25.0)
        except ExternalServiceError as error:
            raise RouteDesignError(
                f"Çizim yol ağına oturtulamadı: {error} Önceki rota korunmuştur."
            ) from error
        snapped_points = tuple(
            replace(
                point,
                latitude=coordinate.latitude,
                longitude=coordinate.longitude,
                coordinate_status="OSRM_YOL_ESLESMESI",
            )
            for point, coordinate in zip(normalized, matched.snapped_coordinates)
        )
        return self._calculate(snapped_points)

    def calculate_automatic(
        self,
        points: tuple[RouteDesignPoint, ...],
        *,
        accept_mismatches: bool = False,
    ) -> RouteCalculation:
        if not points:
            raise RouteDesignValidationError("En az bir adres noktası girilmelidir.")
        resolved: list[RouteDesignPoint] = []
        for index, point in enumerate(points, start=1):
            if point.latitude is not None or point.longitude is not None:
                resolved.append(self._coordinate_point(point))
                continue
            address = point.address_input()
            if not address.has_location_data():
                raise RouteDesignValidationError(
                    f"{index}. noktanın adres veya koordinat bilgisi eksik."
                )
            try:
                response = self.geocoder.search(address)
            except ExternalServiceError as error:
                raise RouteDesignError(str(error)) from error
            if not response.candidates:
                raise RouteDesignError(f"{index}. adres için koordinat bulunamadı.")
            candidate = response.candidates[0]
            assessment = assess_candidate(address, candidate)
            if assessment.mismatches and not accept_mismatches:
                raise AddressConfirmationRequired(
                    index, candidate, assessment.mismatches
                )
            resolved.append(
                replace(
                    point,
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                    coordinate_status="COZUMLENDI",
                )
            )
        return self._calculate(tuple(resolved))

    def recalculate(self, points: tuple[RouteDesignPoint, ...]) -> RouteCalculation:
        return self.calculate_manual(points)

    def insert_point(
        self,
        points: tuple[RouteDesignPoint, ...],
        index: int,
        point: RouteDesignPoint,
    ) -> tuple[RouteDesignPoint, ...]:
        if index < 0 or index > len(points):
            raise RouteDesignValidationError("Noktanın ekleneceği sıra geçersiz.")
        return points[:index] + (point,) + points[index:]

    def update_point(
        self,
        points: tuple[RouteDesignPoint, ...],
        index: int,
        point: RouteDesignPoint,
    ) -> tuple[RouteDesignPoint, ...]:
        self._require_point_index(points, index)
        return points[:index] + (point,) + points[index + 1 :]

    def delete_point(
        self,
        points: tuple[RouteDesignPoint, ...],
        index: int,
    ) -> tuple[RouteDesignPoint, ...]:
        self._require_point_index(points, index)
        return points[:index] + points[index + 1 :]

    def move_point(
        self,
        points: tuple[RouteDesignPoint, ...],
        index: int,
        direction: int,
    ) -> tuple[RouteDesignPoint, ...]:
        self._require_point_index(points, index)
        target = index + direction
        self._require_point_index(points, target)
        mutable = list(points)
        mutable[index], mutable[target] = mutable[target], mutable[index]
        return tuple(mutable)

    def suggest_service_code(self) -> str:
        with self.session_factory() as session:
            codes = [item.servis_kodu for item in ServisRepository(session).list_ordered()]
        numbers = [
            int(match.group(1))
            for code in codes
            if (match := re.fullmatch(r"S_(\d+)", code, flags=re.IGNORECASE))
        ]
        return f"S_{(max(numbers, default=0) + 1):03d}"

    def list_routes(self, search_text: str = "") -> tuple[SavedRouteView, ...]:
        try:
            with self.session_factory() as session:
                routes = SavedRouteRepository(session).search(search_text)
                return tuple(self._view(route) for route in routes)
        except Exception as error:
            logger.exception("Kayıtlı rotalar listelenemedi.")
            raise RouteDesignError("Kayıtlı rotalar listelenemedi.") from error

    def get_route(self, route_id: int) -> SavedRouteView:
        with self.session_factory() as session:
            route = SavedRouteRepository(session).get_detail(route_id)
            if route is None:
                raise RouteNotFoundError("Kayıtlı rota bulunamadı.")
            return self._view(route)

    def build_summary(self, request: RouteSaveRequest) -> RouteSummary:
        route_name = self._route_name(request.route_name)
        labels: list[str] = []
        capacities: list[int] = []
        if request.new_service is not None:
            normalized = self._new_service_values(request.new_service)
            labels.append(
                f"{normalized['service_code']} / {normalized['shift']} / "
                f"{normalized['collar']}"
            )
            capacities.append(int(normalized["capacity"]))
        if request.shift_ids:
            with self.session_factory() as session:
                repository = ServisVardiyasiRepository(session)
                for shift_id in dict.fromkeys(request.shift_ids):
                    shift = repository.get_detail(shift_id)
                    if shift is None:
                        raise RouteDesignValidationError(
                            "Seçilen servis-vardiya kaydı bulunamadı."
                        )
                    labels.append(self._shift_label(shift))
                    capacities.append(shift.kapasite)
        if not labels:
            raise RouteDesignValidationError("En az bir servis-vardiya seçilmelidir.")
        return RouteSummary(
            route_name=route_name,
            service_shifts=tuple(labels),
            capacity=min(capacities),
            distance_km=request.calculation.distance_meters / 1000,
            duration_minutes=request.calculation.duration_seconds / 60,
            point_count=len(request.calculation.points),
            creation_method=self._creation_method(request.creation_method),
        )

    def save_route(
        self,
        request: RouteSaveRequest,
        *,
        route_id: int | None = None,
    ) -> SavedRouteView:
        self.build_summary(request)
        try:
            with session_scope(self.session_factory) as session:
                shift_ids = list(dict.fromkeys(request.shift_ids))
                if request.new_service is not None:
                    shift_ids.append(self._create_service_and_shift(session, request))
                self._validate_shift_ids(session, tuple(shift_ids))
                repository = SavedRouteRepository(session)
                route = repository.get_detail(route_id) if route_id is not None else None
                if route_id is not None and route is None:
                    raise RouteNotFoundError("Güncellenecek rota bulunamadı.")
                if route is None:
                    route = repository.add(self._new_route(request))
                else:
                    self._assign_route(route, request)
                repository.replace_points(route, self._point_models(request.calculation))
                repository.replace_shift_links(route, tuple(shift_ids))
                self._after_route_saved(session, route)
                saved_id = route.id
            return self.get_route(saved_id)
        except (RouteDesignError, RouteNotFoundError):
            raise
        except IntegrityError as error:
            logger.exception("Rota transaction işlemi bütün olarak geri alındı.")
            raise RouteDesignError(
                "Rota kaydedilemedi; servis kodu veya servis-vardiya kaydı zaten var. "
                "Hiçbir değişiklik kaydedilmedi."
            ) from error
        except Exception as error:
            logger.exception("Rota transaction işlemi bütün olarak geri alındı.")
            raise RouteDesignError(
                "Rota kaydedilemedi. İşlemdeki tüm değişiklikler geri alındı."
            ) from error

    def set_route_active(self, route_id: int, active: bool) -> SavedRouteView:
        with session_scope(self.session_factory) as session:
            route = SavedRouteRepository(session).get_detail(route_id)
            if route is None:
                raise RouteNotFoundError("Kayıtlı rota bulunamadı.")
            route.aktif_mi = active
        return self.get_route(route_id)

    def delete_route_controlled(self, route_id: int) -> ControlledDeleteResult:
        with session_scope(self.session_factory) as session:
            repository = SavedRouteRepository(session)
            route = repository.get_detail(route_id)
            if route is None:
                raise RouteNotFoundError("Kayıtlı rota bulunamadı.")
            if repository.historical_assignment_count(route_id):
                route.aktif_mi = False
                return ControlledDeleteResult(
                    False,
                    True,
                    "Bu rota atama/geçmiş ilişkisi içerdiği için silinmedi; pasif yapıldı.",
                )
            repository.delete_physical(route)
            return ControlledDeleteResult(
                True, False, "Kullanılmamış rota kalıcı olarak silindi."
            )

    def _calculate(self, points: tuple[RouteDesignPoint, ...]) -> RouteCalculation:
        if not points:
            raise RouteDesignValidationError("Rota için en az bir başlangıç noktası gerekir.")
        destination = self.factory_destination()
        coordinates = tuple(
            RouteCoordinate(float(point.latitude), float(point.longitude))
            for point in points
        ) + (RouteCoordinate(destination.latitude, destination.longitude),)
        try:
            result = self.router.calculate(coordinates)
        except ExternalServiceError as error:
            raise RouteDesignError(
                f"Rota hesaplanamadı: {error}. Mevcut kontrol noktaları korunmuştur."
            ) from error
        return RouteCalculation(
            points=points,
            destination=destination,
            distance_meters=result.distance_meters,
            duration_seconds=result.duration_seconds,
            geometry=result.geometry,
        )

    def _coordinate_points(
        self, points: tuple[RouteDesignPoint, ...]
    ) -> tuple[RouteDesignPoint, ...]:
        if not points:
            raise RouteDesignValidationError("Rota için en az bir kontrol noktası gerekir.")
        return tuple(self._coordinate_point(point) for point in points if point.active)

    @staticmethod
    def _coordinate_point(point: RouteDesignPoint) -> RouteDesignPoint:
        try:
            latitude = normalize_coordinate(point.latitude, latitude=True)
            longitude = normalize_coordinate(point.longitude, latitude=False)
        except ValueNormalizationError as error:
            raise RouteDesignValidationError(error.message) from error
        if latitude is None or longitude is None:
            raise RouteDesignValidationError(
                "Her kontrol noktası için enlem ve boylam birlikte gereklidir."
            )
        return replace(point, latitude=latitude, longitude=longitude)

    @staticmethod
    def _require_point_index(points: tuple[RouteDesignPoint, ...], index: int) -> None:
        if index < 0 or index >= len(points):
            raise RouteDesignValidationError("Seçilen kontrol noktası bulunamadı.")

    @staticmethod
    def _route_name(value: object) -> str:
        name = normalize_text(value)
        if not name:
            raise RouteDesignValidationError("Rota adı zorunludur.")
        return name

    @staticmethod
    def _creation_method(value: object) -> str:
        method = str(value or "").strip().upper()
        if method not in {"MANUEL", "OTOMATIK"}:
            raise RouteDesignValidationError(
                "Oluşturma yöntemi manuel veya otomatik olmalıdır."
            )
        return method

    @staticmethod
    def _new_service_values(data: NewServiceForRoute) -> dict[str, object]:
        if not data.service_code_confirmed:
            raise RouteDesignValidationError(
                "Önerilen servis kodu kullanıcı tarafından onaylanmalıdır."
            )
        try:
            code = normalize_service_code(data.service_code)
            shift = normalize_shift(data.shift)
            collar = normalize_collar(data.collar)
            capacity = normalize_positive_integer(data.capacity)
        except ValueNormalizationError as error:
            raise RouteDesignValidationError(error.message) from error
        if not code or shift is None or collar is None or capacity is None:
            raise RouteDesignValidationError("Yeni servis-vardiya bilgileri eksik.")
        return {
            "service_code": code,
            "shift": shift,
            "collar": collar,
            "capacity": capacity,
        }

    def _create_service_and_shift(
        self, session: Session, request: RouteSaveRequest
    ) -> int:
        assert request.new_service is not None
        values = self._new_service_values(request.new_service)
        service_repository = ServisRepository(session)
        if service_repository.get_by_code(str(values["service_code"])) is not None:
            raise RouteDesignValidationError("Bu servis kodu zaten kullanılıyor.")
        service = service_repository.add(
            Servis(
                servis_kodu=values["service_code"],
                rota_adi=self._route_name(request.route_name),
                aktif_mi=request.new_service.active,
            )
        )
        shift = ServisVardiyasiRepository(session).add(
            ServisVardiyasi(
                servis_id=service.id,
                vardiya_turu=values["shift"],
                yaka_turu=values["collar"],
                kapasite=values["capacity"],
                rota_adi=self._route_name(request.route_name),
                aktif_mi=request.new_service.active,
            )
        )
        return shift.id

    @staticmethod
    def _validate_shift_ids(session: Session, shift_ids: tuple[int, ...]) -> None:
        if not shift_ids:
            raise RouteDesignValidationError("En az bir servis-vardiya seçilmelidir.")
        repository = ServisVardiyasiRepository(session)
        if any(repository.get_by_id(shift_id) is None for shift_id in shift_ids):
            raise RouteDesignValidationError("Seçilen servis-vardiya kaydı bulunamadı.")

    def _new_route(self, request: RouteSaveRequest) -> Rota:
        route = Rota()
        self._assign_route(route, request)
        return route

    def _assign_route(self, route: Rota, request: RouteSaveRequest) -> None:
        calculation = request.calculation
        first = calculation.points[0]
        route.rota_adi = self._route_name(request.route_name)
        route.olusturma_yontemi = self._creation_method(request.creation_method)
        route.baslangic_adi = first.name or first.address or "Başlangıç"
        route.baslangic_enlem = float(first.latitude)
        route.baslangic_boylam = float(first.longitude)
        route.bitis_adi = calculation.destination.name
        route.bitis_adresi = calculation.destination.address
        route.bitis_enlem = calculation.destination.latitude
        route.bitis_boylam = calculation.destination.longitude
        route.toplam_mesafe_metre = calculation.distance_meters
        route.toplam_sure_saniye = calculation.duration_seconds
        route.geojson = json.dumps(calculation.geometry, ensure_ascii=False)
        route.hesaplanma_tarihi = datetime.now()
        route.aktif_mi = request.active

    @staticmethod
    def _point_models(calculation: RouteCalculation) -> list[RotaKontrolNoktasi]:
        return [
            RotaKontrolNoktasi(
                sira_no=index,
                nokta_adi=point.name,
                acik_adres=point.address,
                mahalle=point.neighborhood,
                sokak=point.street,
                cadde=point.avenue,
                ilce=point.district,
                il=point.city,
                enlem=float(point.latitude),
                boylam=float(point.longitude),
                koordinat_durumu=point.coordinate_status,
                aktif_mi=point.active,
            )
            for index, point in enumerate(calculation.points, start=1)
        ]

    def _after_route_saved(self, session: Session, route: Rota) -> None:
        """Transaction rollback testlerinde güvenli arıza enjeksiyon noktası."""

    @staticmethod
    def _shift_label(shift: ServisVardiyasi) -> str:
        return (
            f"{shift.servis.servis_kodu} / {shift.vardiya_turu.value} / "
            f"{shift.yaka_turu.value}"
        )

    @classmethod
    def _view(cls, route: Rota) -> SavedRouteView:
        links = tuple(
            link for link in route.servis_vardiya_baglantilari if link.aktif_mi
        )
        return SavedRouteView(
            id=route.id,
            route_name=route.rota_adi,
            creation_method=route.olusturma_yontemi,
            active=route.aktif_mi,
            start_name=route.baslangic_adi,
            destination_name=route.bitis_adi,
            distance_meters=route.toplam_mesafe_metre,
            duration_seconds=route.toplam_sure_saniye,
            geometry=json.loads(route.geojson),
            points=tuple(
                RouteDesignPoint(
                    name=point.nokta_adi,
                    address=point.acik_adres,
                    neighborhood=point.mahalle,
                    street=point.sokak,
                    avenue=point.cadde,
                    district=point.ilce,
                    city=point.il,
                    latitude=point.enlem,
                    longitude=point.boylam,
                    coordinate_status=point.koordinat_durumu,
                    active=point.aktif_mi,
                )
                for point in route.kontrol_noktalari
            ),
            shift_ids=tuple(link.servis_vardiya_id for link in links),
            shift_labels=tuple(cls._shift_label(link.servis_vardiyasi) for link in links),
        )
