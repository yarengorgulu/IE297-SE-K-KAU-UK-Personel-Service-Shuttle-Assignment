"""OSRM rota API istemcisi ve cevap doğrulaması."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

from services.external_http import (
    ExternalServiceError,
    InvalidExternalResponseError,
    JsonHttpClient,
)
from services.geocoding import APPLICATION_USER_AGENT


logger = logging.getLogger(__name__)
DEFAULT_OSRM_URL = os.getenv(
    "SERVIS_ATAMA_OSRM_URL",
    "https://router.project-osrm.org/route/v1/driving",
)
DEFAULT_OSRM_TABLE_URL = os.getenv(
    "SERVIS_ATAMA_OSRM_TABLE_URL",
    "https://router.project-osrm.org/table/v1/driving",
)
DEFAULT_OSRM_MATCH_URL = os.getenv(
    "SERVIS_ATAMA_OSRM_MATCH_URL",
    "https://router.project-osrm.org/match/v1/driving",
)


class RoutingError(ExternalServiceError):
    """OSRM rota hesabı tamamlanamadığında üretilir."""


@dataclass(frozen=True, slots=True)
class RouteCoordinate:
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class RoutingResult:
    distance_meters: float
    duration_seconds: float
    geometry: dict[str, object]


@dataclass(frozen=True, slots=True)
class OptimizedRoutingResult:
    route: RoutingResult
    pickup_order: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class MatchedTraceResult:
    snapped_coordinates: tuple[RouteCoordinate, ...]
    route: RoutingResult


class OsrmRoutingService:
    def __init__(
        self,
        *,
        http_client: JsonHttpClient | None = None,
        base_url: str = DEFAULT_OSRM_URL,
        table_url: str = DEFAULT_OSRM_TABLE_URL,
        match_url: str = DEFAULT_OSRM_MATCH_URL,
        timeout_seconds: float = 25.0,
    ) -> None:
        self.http_client = http_client or JsonHttpClient()
        self.base_url = base_url.rstrip("/")
        self.table_url = table_url.rstrip("/")
        self.match_url = match_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def match_trace(
        self,
        coordinates: tuple[RouteCoordinate, ...],
        *,
        radius_meters: float = 25.0,
    ) -> MatchedTraceResult:
        """Fareyle çizilen izi yakınındaki kesintisiz sürülebilir yol ağına oturtur."""
        if len(coordinates) < 2:
            raise RoutingError("Yola oturtmak için en az iki çizim noktası gerekir.")
        if len(coordinates) > 100:
            raise RoutingError("Tek çizimde en fazla 100 örnek nokta kullanılabilir.")
        if not math.isfinite(radius_meters) or radius_meters <= 0:
            raise RoutingError("Yol eşleştirme yarıçapı geçersiz.")
        coordinate_path = ";".join(
            f"{point.longitude:.8f},{point.latitude:.8f}" for point in coordinates
        )
        radiuses = ";".join(f"{radius_meters:.1f}" for _ in coordinates)
        try:
            payload = self.http_client.get_json(
                f"{self.match_url}/{coordinate_path}",
                params={
                    "overview": "full",
                    "geometries": "geojson",
                    "steps": "false",
                    "tidy": "true",
                    "gaps": "split",
                    "radiuses": radiuses,
                },
                headers={"User-Agent": APPLICATION_USER_AGENT},
                timeout=min(self.timeout_seconds, 12.0),
            )
        except ExternalServiceError:
            logger.warning("OSRM yol eşleştirme isteği tamamlanamadı.")
            raise
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            message = payload.get("message") if isinstance(payload, dict) else None
            raise RoutingError(
                str(message or "Çizim sürülebilir yol ağıyla eşleştirilemedi.")
            )
        tracepoints = payload.get("tracepoints")
        if not isinstance(tracepoints, list) or len(tracepoints) != len(coordinates):
            raise InvalidExternalResponseError(
                "Yol eşleştirme servisinin iz noktaları geçersiz."
            )
        snapped: list[RouteCoordinate] = []
        for source, tracepoint in zip(coordinates, tracepoints):
            if not isinstance(tracepoint, dict):
                raise RoutingError(
                    f"Çizimin bir bölümü {radius_meters:.0f} metre içinde "
                    "sürülebilir bir yolla eşleşmedi. Çizim kabul edilmedi."
                )
            location = tracepoint.get("location")
            if not isinstance(location, list) or len(location) < 2:
                raise InvalidExternalResponseError(
                    "Yol eşleştirme servisinin koordinat cevabı geçersiz."
                )
            try:
                candidate = RouteCoordinate(float(location[1]), float(location[0]))
            except (TypeError, ValueError) as error:
                raise InvalidExternalResponseError(
                    "Yol eşleştirme servisinin koordinat cevabı geçersiz."
                ) from error
            maximum_snap_distance = radius_meters * 3.0 + 1.0
            if _haversine_meters(source, candidate) > maximum_snap_distance:
                raise RoutingError(
                    f"Çizimin bir bölümü sürülebilir yoldan {radius_meters * 3:.0f} "
                    "metreden fazla uzakta. Çizim kabul edilmedi."
                )
            snapped.append(candidate)
        matchings = payload.get("matchings")
        if not isinstance(matchings, list) or len(matchings) != 1:
            raise RoutingError(
                "Çizim kesintisiz tek bir sürülebilir güzergâhla eşleşmedi. "
                "Yalnızca yolların üzerinden yeniden çizin."
            )
        route = self._routing_result(matchings[0], response_name="yol eşleştirme")
        return MatchedTraceResult(tuple(snapped), route)

    def calculate_optimized_to_destination(
        self,
        pickups: tuple[RouteCoordinate, ...],
        destination: RouteCoordinate,
    ) -> OptimizedRoutingResult:
        """Toplama noktalarını optimize eder ve sabit varışta biten rotayı üretir."""
        if not pickups:
            raise RoutingError("Rota hesaplamak için en az bir toplama noktası gerekir.")
        if len(pickups) > 99:
            raise RoutingError(
                "Tek rotada en fazla 99 toplama noktası optimize edilebilir. "
                "Hizmet bölgesini gerçek duraklara ayırın."
            )
        all_coordinates = pickups + (destination,)
        durations = self._duration_matrix(all_coordinates)
        pickup_order = optimize_fixed_destination_order(durations)
        ordered_coordinates = tuple(pickups[index] for index in pickup_order) + (
            destination,
        )
        return OptimizedRoutingResult(
            route=self.calculate(ordered_coordinates),
            pickup_order=pickup_order,
        )

    def _duration_matrix(
        self,
        coordinates: tuple[RouteCoordinate, ...],
    ) -> tuple[tuple[float, ...], ...]:
        coordinate_path = ";".join(
            f"{point.longitude:.8f},{point.latitude:.8f}" for point in coordinates
        )
        try:
            payload = self.http_client.get_json(
                f"{self.table_url}/{coordinate_path}",
                params={"annotations": "duration"},
                headers={"User-Agent": APPLICATION_USER_AGENT},
                timeout=self.timeout_seconds,
            )
        except ExternalServiceError:
            logger.warning("OSRM süre matrisi isteği tamamlanamadı.")
            raise
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            message = payload.get("message") if isinstance(payload, dict) else None
            raise RoutingError(
                str(message or "Rota servisi noktalar arasında süre matrisi oluşturamadı.")
            )
        raw_durations = payload.get("durations")
        size = len(coordinates)
        if not isinstance(raw_durations, list) or len(raw_durations) != size:
            raise InvalidExternalResponseError("Rota servisinin süre matrisi geçersiz.")
        matrix: list[tuple[float, ...]] = []
        for row in raw_durations:
            if not isinstance(row, list) or len(row) != size:
                raise InvalidExternalResponseError("Rota servisinin süre matrisi geçersiz.")
            parsed_row: list[float] = []
            for value in row:
                try:
                    duration = float(value)
                except (TypeError, ValueError) as error:
                    raise RoutingError(
                        "Güzergâh noktalarından biri diğer noktalara karayoluyla bağlanamıyor."
                    ) from error
                if duration < 0 or not math.isfinite(duration):
                    raise InvalidExternalResponseError(
                        "Rota servisinin süre matrisi geçersiz."
                    )
                parsed_row.append(duration)
            matrix.append(tuple(parsed_row))
        return tuple(matrix)

    def calculate(self, coordinates: tuple[RouteCoordinate, ...]) -> RoutingResult:
        if len(coordinates) < 2:
            raise RoutingError("Rota hesaplamak için en az iki koordinatlı nokta gerekir.")
        coordinate_path = ";".join(
            f"{point.longitude:.8f},{point.latitude:.8f}" for point in coordinates
        )
        try:
            payload = self.http_client.get_json(
                f"{self.base_url}/{coordinate_path}",
                params={
                    "overview": "full",
                    "geometries": "geojson",
                    "steps": "false",
                },
                headers={"User-Agent": APPLICATION_USER_AGENT},
                timeout=self.timeout_seconds,
            )
        except ExternalServiceError:
            logger.warning("OSRM rota isteği tamamlanamadı.")
            raise
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            message = payload.get("message") if isinstance(payload, dict) else None
            raise RoutingError(
                str(message or "Rota servisi bu noktalar için rota bulamadı.")
            )
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise InvalidExternalResponseError("Rota servisi geçersiz cevap döndürdü.")
        return self._routing_result(routes[0], response_name="rota")

    @staticmethod
    def _routing_result(
        route: dict[str, object],
        *,
        response_name: str,
    ) -> RoutingResult:
        geometry = route.get("geometry")
        try:
            distance = float(route["distance"])
            duration = float(route["duration"])
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidExternalResponseError(
                f"{response_name.capitalize()} servisinin mesafe veya süre cevabı geçersiz."
            ) from error
        if (
            distance < 0
            or duration < 0
            or not isinstance(geometry, dict)
            or geometry.get("type") != "LineString"
            or not isinstance(geometry.get("coordinates"), list)
        ):
            raise InvalidExternalResponseError(
                f"{response_name.capitalize()} servisi geçersiz geometri döndürdü."
            )
        return RoutingResult(
            distance_meters=distance,
            duration_seconds=duration,
            geometry=geometry,
        )


def _haversine_meters(first: RouteCoordinate, second: RouteCoordinate) -> float:
    earth_radius = 6_371_000.0
    latitude_1 = math.radians(first.latitude)
    latitude_2 = math.radians(second.latitude)
    latitude_delta = latitude_2 - latitude_1
    longitude_delta = math.radians(second.longitude - first.longitude)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_1)
        * math.cos(latitude_2)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * earth_radius * math.asin(min(1.0, math.sqrt(value)))


def optimize_fixed_destination_order(
    durations: tuple[tuple[float, ...], ...],
) -> tuple[int, ...]:
    """Serbest başlangıçlı, bütün toplamalardan geçen, sabit bitişli sıra bulur."""
    size = len(durations)
    if size < 2 or any(len(row) != size for row in durations):
        raise RoutingError("Rota optimizasyon matrisi geçersiz.")
    pickup_count = size - 1
    if pickup_count <= 11:
        return _exact_fixed_destination_order(durations)
    return _heuristic_fixed_destination_order(durations)


def _exact_fixed_destination_order(
    durations: tuple[tuple[float, ...], ...],
) -> tuple[int, ...]:
    pickup_count = len(durations) - 1
    full_mask = (1 << pickup_count) - 1
    costs: dict[tuple[int, int], float] = {
        (1 << index, index): 0.0 for index in range(pickup_count)
    }
    previous: dict[tuple[int, int], int] = {}
    for mask in range(1, full_mask + 1):
        for last in range(pickup_count):
            current = costs.get((mask, last))
            if current is None:
                continue
            for next_index in range(pickup_count):
                bit = 1 << next_index
                if mask & bit:
                    continue
                next_mask = mask | bit
                candidate = current + durations[last][next_index]
                key = (next_mask, next_index)
                if candidate < costs.get(key, math.inf):
                    costs[key] = candidate
                    previous[key] = last
    destination_index = pickup_count
    last = min(
        range(pickup_count),
        key=lambda index: costs[(full_mask, index)]
        + durations[index][destination_index],
    )
    order: list[int] = []
    mask = full_mask
    while True:
        order.append(last)
        prior = previous.get((mask, last))
        if prior is None:
            break
        mask ^= 1 << last
        last = prior
    order.reverse()
    return tuple(order)


def _heuristic_fixed_destination_order(
    durations: tuple[tuple[float, ...], ...],
) -> tuple[int, ...]:
    pickup_count = len(durations) - 1
    destination_index = pickup_count
    best_order: tuple[int, ...] | None = None
    best_cost = math.inf
    for start in range(pickup_count):
        remaining = set(range(pickup_count))
        remaining.remove(start)
        order = [start]
        while remaining:
            current = order[-1]
            next_index = min(
                remaining,
                key=lambda index: durations[current][index],
            )
            order.append(next_index)
            remaining.remove(next_index)
        candidate = tuple(order)
        cost = _path_cost(candidate, destination_index, durations)
        if cost < best_cost:
            best_order, best_cost = candidate, cost
    if best_order is None:
        raise RoutingError("Rota sırası oluşturulamadı.")

    improved = list(best_order)
    for _ in range(10):
        changed = False
        current_cost = _path_cost(tuple(improved), destination_index, durations)
        for start in range(len(improved) - 1):
            for end in range(start + 1, len(improved)):
                candidate = (
                    improved[:start]
                    + list(reversed(improved[start : end + 1]))
                    + improved[end + 1 :]
                )
                candidate_cost = _path_cost(
                    tuple(candidate),
                    destination_index,
                    durations,
                )
                if candidate_cost + 1e-9 < current_cost:
                    improved = candidate
                    current_cost = candidate_cost
                    changed = True
        if not changed:
            break
    return tuple(improved)


def _path_cost(
    order: tuple[int, ...],
    destination_index: int,
    durations: tuple[tuple[float, ...], ...],
) -> float:
    if not order:
        return 0.0
    cost = sum(
        durations[first][second]
        for first, second in zip(order, order[1:])
    )
    return cost + durations[order[-1]][destination_index]
