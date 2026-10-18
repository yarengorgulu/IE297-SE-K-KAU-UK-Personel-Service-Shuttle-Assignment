"""Personelin kayıtlı rota geometrisine en kısa uzaklığını hesaplar."""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Iterable


EARTH_RADIUS_METERS = 6_371_008.8


@dataclass(frozen=True, slots=True)
class NearestRoutePoint:
    latitude: float
    longitude: float
    distance_meters: float
    segment_index: int
    segment_fraction: float


def haversine_distance_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """İki koordinat arasındaki küresel uzaklığı metre olarak döndürür."""
    lat_a, lat_b = radians(latitude_a), radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = radians(longitude_b - longitude_a)
    value = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * asin(min(1.0, sqrt(value)))


def _project_to_local_meters(
    latitude: float,
    longitude: float,
    origin_latitude: float,
    origin_longitude: float,
) -> tuple[float, float]:
    scale = cos(radians(origin_latitude))
    x = radians(longitude - origin_longitude) * EARTH_RADIUS_METERS * scale
    y = radians(latitude - origin_latitude) * EARTH_RADIUS_METERS
    return x, y


def nearest_point_on_linestring(
    latitude: float,
    longitude: float,
    coordinates: Iterable[Iterable[float]],
) -> NearestRoutePoint:
    """GeoJSON ``[boylam, enlem]`` çizgisinin tamamındaki en yakın noktayı bulur."""
    points = tuple((float(item[1]), float(item[0])) for item in coordinates)
    if len(points) < 2:
        raise ValueError("Rota geometrisi en az iki koordinat içermelidir.")

    best: NearestRoutePoint | None = None
    for index, ((lat_a, lon_a), (lat_b, lon_b)) in enumerate(zip(points, points[1:])):
        ax, ay = _project_to_local_meters(lat_a, lon_a, latitude, longitude)
        bx, by = _project_to_local_meters(lat_b, lon_b, latitude, longitude)
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        fraction = 0.0 if denominator == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denominator))
        nearest_lat = lat_a + (lat_b - lat_a) * fraction
        nearest_lon = lon_a + (lon_b - lon_a) * fraction
        distance = haversine_distance_meters(
            latitude, longitude, nearest_lat, nearest_lon
        )
        candidate = NearestRoutePoint(
            latitude=nearest_lat,
            longitude=nearest_lon,
            distance_meters=distance,
            segment_index=index,
            segment_fraction=fraction,
        )
        if best is None or candidate.distance_meters < best.distance_meters:
            best = candidate

    assert best is not None
    return best


def coordinates_from_geojson(geometry: object) -> tuple[tuple[float, float], ...]:
    """Geçerli bir GeoJSON LineString içinden güvenli koordinat dizisi çıkarır."""
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise ValueError("Kayıtlı rota geometrisi LineString olmalıdır.")
    raw = geometry.get("coordinates")
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("Kayıtlı rota geometrisi en az iki nokta içermelidir.")
    coordinates: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            raise ValueError("Kayıtlı rota koordinatı geçersiz.")
        longitude, latitude = float(item[0]), float(item[1])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Kayıtlı rota koordinatı geçerli aralığın dışında.")
        coordinates.append((longitude, latitude))
    return tuple(coordinates)

