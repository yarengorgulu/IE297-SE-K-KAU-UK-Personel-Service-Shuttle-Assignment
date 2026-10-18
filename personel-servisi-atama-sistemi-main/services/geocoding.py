"""Nominatim adres arama, sonuç normalizasyonu ve adres karşılaştırma kuralları."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from normalization.address import classify_road, extract_address_roads
from normalization.text import comparison_key, normalize_text
from services.external_http import (
    ExternalServiceError,
    InvalidExternalResponseError,
    JsonHttpClient,
)


logger = logging.getLogger(__name__)

DEFAULT_NOMINATIM_URL = os.getenv(
    "SERVIS_ATAMA_NOMINATIM_URL",
    "https://nominatim.openstreetmap.org/search",
)
DEFAULT_NOMINATIM_REVERSE_URL = os.getenv(
    "SERVIS_ATAMA_NOMINATIM_REVERSE_URL",
    "https://nominatim.openstreetmap.org/reverse",
)
APPLICATION_USER_AGENT = os.getenv(
    "SERVIS_ATAMA_USER_AGENT",
    "PersonelServisiAtamaSistemi/1.0",
)


class GeocodingError(ExternalServiceError):
    """Nominatim araması tamamlanamadığında üretilir."""


@dataclass(frozen=True, slots=True)
class AddressInput:
    address: str | None
    city: str | None
    district: str | None
    neighborhood: str | None
    street: str | None = None
    avenue: str | None = None

    def resolved_street(self) -> str | None:
        return self.street or extract_address_roads(self.address).street

    def resolved_avenue(self) -> str | None:
        return self.avenue or extract_address_roads(self.address).avenue

    def full_query(self) -> str:
        return _join_query(
            self.address,
            self.street,
            self.avenue,
            self.neighborhood,
            self.district,
            self.city,
            "Türkiye",
        )

    def road_query(self) -> str:
        return _join_query(
            self.resolved_street(),
            self.resolved_avenue(),
            self.neighborhood,
            self.district,
            self.city,
            "Türkiye",
        )

    def street_query(self) -> str:
        """Eski çağrılar için yol düzeyi sorgu adı."""
        return self.road_query()

    def fallback_query(self) -> str:
        return _join_query(
            self.neighborhood,
            self.district,
            self.city,
            "Türkiye",
        )

    def controlled_queries(self) -> tuple[str, ...]:
        queries = (
            self.full_query(),
            self.road_query(),
            self.fallback_query(),
            _join_query(self.district, self.city, "Türkiye"),
        )
        return tuple(dict.fromkeys(query for query in queries if query))

    def has_location_data(self) -> bool:
        return any(
            normalize_text(value) is not None
            for value in (
                self.address,
                self.street,
                self.avenue,
                self.neighborhood,
                self.district,
                self.city,
            )
        )

    def signature(self) -> str:
        normalized = "|".join(
            comparison_key(value) or ""
            for value in (
                self.address,
                self.city,
                self.district,
                self.neighborhood,
                self.street,
                self.avenue,
            )
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GeocodingCandidate:
    place_id: str
    display_name: str
    latitude: float
    longitude: float
    city: str | None
    district: str | None
    neighborhood: str | None
    street: str | None = None
    avenue: str | None = None
    result_type: str | None = None
    query_level: int = 0
    reverse_checked: bool = False
    neighborhood_area_verified: bool = False


@dataclass(frozen=True, slots=True)
class AddressMismatch:
    field: str
    expected: str
    found: str
    kind: str = "mismatch"

    @property
    def message(self) -> str:
        return f"{self.field}: mevcut '{self.expected}', bulunan '{self.found}'"


@dataclass(frozen=True, slots=True)
class GeocodingSearchResponse:
    candidates: tuple[GeocodingCandidate, ...]
    used_fallback: bool
    queries_attempted: int = 0


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    score: int
    status: str
    details: tuple[str, ...]
    mismatches: tuple[AddressMismatch, ...]


def _join_query(*values: object) -> str:
    parts: list[str] = []
    keys: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        key = _expression_key(normalized)
        if normalized is None or key is None:
            continue
        if any(_contains_expression(existing, key) for existing in keys):
            continue
        parts.append(normalized)
        keys.append(key)
    return ", ".join(parts)


def _expression_key(value: object) -> str | None:
    key = comparison_key(value)
    if key is None:
        return None
    aliases = {
        "SK": "SOKAK",
        "SOK": "SOKAK",
        "SOKAĞI": "SOKAK",
        "CD": "CADDE",
        "CAD": "CADDE",
        "CADDESİ": "CADDE",
        "BLV": "BULVAR",
        "BULVARI": "BULVAR",
        "MAH": "MAHALLE",
        "MH": "MAHALLE",
        "MAHALLESİ": "MAHALLE",
    }
    tokens = re.findall(r"\w+", key, flags=re.UNICODE)
    return " ".join(aliases.get(token, token) for token in tokens)


def _contains_expression(container: str, expression: str) -> bool:
    return f" {expression} " in f" {container} "


def _comparison_value(field: str, value: object) -> str | None:
    key = _expression_key(value)
    if key is None:
        return None
    if field == "Mahalle":
        suffixes = (" MAHALLESİ", " MAHALLE", " MAH", " MH")
        for suffix in suffixes:
            if key.endswith(suffix):
                return key[: -len(suffix)].strip()
    return key


def _expected_components(address: AddressInput) -> tuple[tuple[str, str | None], ...]:
    return (
        ("İl", address.city),
        ("İlçe", address.district),
        ("Mahalle", address.neighborhood),
        ("Sokak", address.resolved_street()),
        ("Cadde", address.resolved_avenue()),
    )


def _candidate_components(
    candidate: GeocodingCandidate,
) -> tuple[tuple[str, str | None], ...]:
    return (
        ("İl", candidate.city),
        ("İlçe", candidate.district),
        ("Mahalle", candidate.neighborhood),
        ("Sokak", candidate.street),
        ("Cadde", candidate.avenue),
    )


def _display_contains(field: str, display_name: str, expected: object) -> bool:
    display_key = _comparison_value(field, display_name)
    expected_key = _comparison_value(field, expected)
    if not display_key or not expected_key:
        return False
    return _contains_expression(display_key, expected_key)


def _field_state(
    field: str,
    expected: object,
    found: object,
    candidate: GeocodingCandidate,
) -> str:
    expected_key = _comparison_value(field, expected)
    if expected_key is None:
        return "not_expected"
    found_key = _comparison_value(field, found)
    if found_key == expected_key:
        return "matched"
    if found_key is not None:
        return "mismatch"
    if _display_contains(field, candidate.display_name, expected):
        return "matched_text"
    if field == "Mahalle" and candidate.neighborhood_area_verified:
        return "matched_area"
    return "missing"


def compare_address(
    expected: AddressInput,
    candidate: GeocodingCandidate,
) -> tuple[AddressMismatch, ...]:
    mismatches: list[AddressMismatch] = []
    found_by_field = dict(_candidate_components(candidate))
    for label, expected_value in _expected_components(expected):
        state = _field_state(
            label,
            expected_value,
            found_by_field[label],
            candidate,
        )
        if state in {"missing", "mismatch"}:
            mismatches.append(
                AddressMismatch(
                    field=label,
                    expected=normalize_text(expected_value) or "",
                    found=(
                        normalize_text(found_by_field[label])
                        or "Sonuçta belirtilmedi"
                    ),
                    kind=state,
                )
            )
    return tuple(mismatches)


def assess_candidate(
    expected: AddressInput,
    candidate: GeocodingCandidate,
) -> CandidateAssessment:
    """Adayı adres bileşenleri, hassasiyet ve doğrulama kanıtlarıyla puanlar."""

    weights = {"İl": 10, "İlçe": 20, "Mahalle": 25, "Sokak": 25, "Cadde": 25}
    found_by_field = dict(_candidate_components(candidate))
    matched: list[str] = []
    matched_from_text: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    area_verified = False
    score = max(0, 12 - (candidate.query_level * 3))

    for field, expected_value in _expected_components(expected):
        state = _field_state(
            field,
            expected_value,
            found_by_field[field],
            candidate,
        )
        if state == "not_expected":
            continue
        weight = weights[field]
        if state.startswith("matched"):
            matched.append(field)
            if state == "matched_text":
                matched_from_text.append(field)
            score += weight
            area_verified = area_verified or state == "matched_area"
        elif state == "missing":
            missing.append(field)
            score -= max(3, weight // 5)
        else:
            mismatched.append(field)
            score -= weight * 2

    result_type = comparison_key(candidate.result_type) or ""
    precise_types = {
        "HOUSE",
        "BUILDING",
        "RESIDENTIAL",
        "ROAD",
        "STREET",
        "PEDESTRIAN",
    }
    general_types = {
        "STATE",
        "PROVINCE",
        "COUNTY",
        "CITY",
        "TOWN",
        "DISTRICT",
        "MUNICIPALITY",
    }
    expects_road = bool(expected.resolved_street() or expected.resolved_avenue())
    if result_type in precise_types:
        score += 12
    elif result_type in general_types and expects_road:
        score -= 20
    if candidate.reverse_checked:
        score += 5

    mismatches = compare_address(expected, candidate)
    explicit_mismatches = [item for item in mismatches if item.kind == "mismatch"]
    if explicit_mismatches:
        status = "UYUŞMAZLIK"
    elif result_type in general_types and expects_road and not ({"Sokak", "Cadde"} & set(matched)):
        status = "YETERSİZ / GENEL SONUÇ"
    elif area_verified:
        status = "MAHALLE ALANINDA DOĞRULANDI"
    elif not missing and candidate.reverse_checked:
        status = "DOĞRULANDI"
    elif not missing:
        status = "UYUMLU"
    elif candidate.reverse_checked and {"İl", "İlçe"}.issubset(set(matched)):
        status = "KISMEN DOĞRULANDI"
    else:
        status = "EK KONTROL GEREKLİ"

    details: list[str] = []
    if matched:
        details.append("Eşleşen: " + ", ".join(matched))
    if matched_from_text:
        details.append(
            "Bulunan adres metninden doğrulanan: " + ", ".join(matched_from_text)
        )
    if missing:
        details.append("Sonuçta eksik: " + ", ".join(missing))
    if mismatched:
        details.append("Uyuşmayan: " + ", ".join(mismatched))
    if candidate.reverse_checked:
        details.append("Ters adres kontrolü yapıldı")
    if area_verified:
        details.append("Koordinat kayıtlı mahalle alanının içinde")
    return CandidateAssessment(
        score=max(0, min(100, score)),
        status=status,
        details=tuple(details),
        mismatches=mismatches,
    )


class NominatimGeocodingService:
    """Nominatim kullanım politikasına uygun, tek iş parçacıklı oran sınırlı istemci."""

    _rate_lock = threading.Lock()
    _last_request_time = 0.0

    def __init__(
        self,
        *,
        http_client: JsonHttpClient | None = None,
        base_url: str = DEFAULT_NOMINATIM_URL,
        reverse_url: str = DEFAULT_NOMINATIM_REVERSE_URL,
        timeout_seconds: float = 15.0,
        minimum_interval_seconds: float = 1.05,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.http_client = http_client or JsonHttpClient()
        self.base_url = base_url
        self.reverse_url = reverse_url
        self.timeout_seconds = timeout_seconds
        self.minimum_interval_seconds = minimum_interval_seconds
        self.clock = clock
        self.sleeper = sleeper

    def search(self, address: AddressInput) -> GeocodingSearchResponse:
        if not address.has_location_data():
            raise GeocodingError("Adres çözümleme için yeterli konum bilgisi bulunmuyor.")
        candidates: dict[str, GeocodingCandidate] = {}
        queries_attempted = 0
        for index, query in enumerate(address.controlled_queries()):
            if index == 3 and candidates:
                break
            queries_attempted += 1
            for candidate in self._search_query(query, query_level=index):
                self._merge_candidate(candidates, candidate)
            ranked = self._rank(address, candidates.values())
            if ranked and assess_candidate(address, ranked[0]).score >= 85:
                break

        ranked = self._rank(address, candidates.values())[:5]
        verified: list[GeocodingCandidate] = []
        for index, candidate in enumerate(ranked):
            assessment = assess_candidate(address, candidate)
            needs_reverse = bool(assessment.mismatches)
            if index == 0 and needs_reverse:
                candidate = self._reverse_candidate(candidate)
            verified.append(candidate)

        if address.neighborhood:
            needs_area_check = [
                item
                for item in verified
                if any(
                    mismatch.field == "Mahalle" and mismatch.kind == "missing"
                    for mismatch in compare_address(address, item)
                )
            ]
            if needs_area_check:
                areas = self._search_neighborhood_areas(address)
                verified = [
                    replace(
                        item,
                        neighborhood_area_verified=(
                            item.neighborhood_area_verified
                            or (
                                item in needs_area_check
                                and any(
                                    _area_contains(area, item.latitude, item.longitude)
                                    for area in areas
                                )
                            )
                        ),
                    )
                    for item in verified
                ]

        ranked = self._rank(address, verified)[:5]
        return GeocodingSearchResponse(
            candidates=tuple(ranked),
            used_fallback=queries_attempted > 1,
            queries_attempted=queries_attempted,
        )

    def _search_query(
        self,
        query: str,
        *,
        query_level: int,
    ) -> list[GeocodingCandidate]:
        self._wait_for_rate_limit()
        try:
            payload = self.http_client.get_json(
                self.base_url,
                params={
                    "q": query,
                    "format": "geocodejson",
                    "addressdetails": 1,
                    "limit": 5,
                    "countrycodes": "tr",
                    "accept-language": "tr",
                },
                headers={"User-Agent": APPLICATION_USER_AGENT},
                timeout=self.timeout_seconds,
            )
        except ExternalServiceError:
            logger.warning("Nominatim isteği tamamlanamadı; adres içeriği loglanmadı.")
            raise
        return self._parse_candidates(payload, query_level=query_level)

    def _reverse_candidate(
        self,
        candidate: GeocodingCandidate,
    ) -> GeocodingCandidate:
        self._wait_for_rate_limit()
        try:
            payload = self.http_client.get_json(
                self.reverse_url,
                params={
                    "lat": candidate.latitude,
                    "lon": candidate.longitude,
                    "format": "geocodejson",
                    "addressdetails": 1,
                    "zoom": 18,
                    "accept-language": "tr",
                },
                headers={"User-Agent": APPLICATION_USER_AGENT},
                timeout=self.timeout_seconds,
            )
            results = self._parse_candidates(
                payload,
                query_level=candidate.query_level,
            )
        except ExternalServiceError:
            logger.warning("Nominatim ters adres doğrulaması tamamlanamadı.")
            return candidate
        if not results:
            return replace(candidate, reverse_checked=True)
        reverse = results[0]
        return replace(
            candidate,
            city=candidate.city or reverse.city,
            district=candidate.district or reverse.district,
            neighborhood=candidate.neighborhood or reverse.neighborhood,
            street=candidate.street or reverse.street,
            avenue=candidate.avenue or reverse.avenue,
            result_type=candidate.result_type or reverse.result_type,
            reverse_checked=True,
        )

    def _search_neighborhood_areas(
        self,
        address: AddressInput,
    ) -> tuple[dict[str, object], ...]:
        self._wait_for_rate_limit()
        try:
            payload = self.http_client.get_json(
                self.base_url,
                params={
                    "q": address.fallback_query(),
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "featureType": "settlement",
                    "polygon_geojson": 1,
                    "limit": 5,
                    "countrycodes": "tr",
                    "accept-language": "tr",
                },
                headers={"User-Agent": APPLICATION_USER_AGENT},
                timeout=self.timeout_seconds,
            )
        except ExternalServiceError:
            logger.warning("Nominatim mahalle alanı isteği tamamlanamadı.")
            return ()
        if not isinstance(payload, list):
            return ()
        areas: list[dict[str, object]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            display_name = str(item.get("display_name") or item.get("name") or "")
            if not _display_contains("Mahalle", display_name, address.neighborhood):
                details = item.get("address")
                if not isinstance(details, dict) or not any(
                    _comparison_value("Mahalle", details.get(key))
                    == _comparison_value("Mahalle", address.neighborhood)
                    for key in ("neighbourhood", "suburb", "quarter", "village")
                ):
                    continue
            areas.append(item)
        return tuple(areas)

    @staticmethod
    def _merge_candidate(
        candidates: dict[str, GeocodingCandidate],
        candidate: GeocodingCandidate,
    ) -> None:
        key = candidate.place_id or (
            f"{candidate.latitude:.7f}:{candidate.longitude:.7f}"
        )
        current = candidates.get(key)
        if current is None:
            candidates[key] = candidate
            return
        candidates[key] = replace(
            current,
            city=current.city or candidate.city,
            district=current.district or candidate.district,
            neighborhood=current.neighborhood or candidate.neighborhood,
            street=current.street or candidate.street,
            avenue=current.avenue or candidate.avenue,
            result_type=current.result_type or candidate.result_type,
            query_level=min(current.query_level, candidate.query_level),
        )

    @staticmethod
    def _rank(
        address: AddressInput,
        candidates: Iterable[GeocodingCandidate],
    ) -> list[GeocodingCandidate]:
        return sorted(
            list(candidates),
            key=lambda item: (
                -assess_candidate(address, item).score,
                item.query_level,
                item.display_name,
            ),
        )

    def _parse_candidates(
        self,
        payload: object,
        *,
        query_level: int,
    ) -> list[GeocodingCandidate]:
        items: list[object]
        geocodejson = isinstance(payload, dict) and isinstance(payload.get("features"), list)
        if geocodejson:
            items = list(payload["features"])
        elif isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and ("lat" in payload or "geometry" in payload):
            items = [payload]
        else:
            raise InvalidExternalResponseError(
                "Adres servisi beklenmeyen bir cevap döndürdü."
            )
        candidates: list[GeocodingCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            properties = item.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            geocoding = properties.get("geocoding")
            geocoding = geocoding if isinstance(geocoding, dict) else {}
            geometry = item.get("geometry")
            geometry = geometry if isinstance(geometry, dict) else {}
            coordinates = geometry.get("coordinates")
            try:
                if geocodejson and isinstance(coordinates, list) and len(coordinates) >= 2:
                    longitude = float(coordinates[0])
                    latitude = float(coordinates[1])
                else:
                    latitude = float(item["lat"])
                    longitude = float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
            raw_address = item.get("address")
            details = raw_address if isinstance(raw_address, dict) else geocoding
            roads = classify_road(
                _first(details, "street", "road", "pedestrian", "residential")
            )
            candidates.append(
                GeocodingCandidate(
                    place_id=str(
                        item.get("place_id")
                        or geocoding.get("osm_id")
                        or geocoding.get("place_id")
                        or ""
                    ),
                    display_name=str(
                        item.get("display_name")
                        or geocoding.get("label")
                        or geocoding.get("name")
                        or "İsimsiz sonuç"
                    ),
                    latitude=latitude,
                    longitude=longitude,
                    city=_first(details, "province", "state", "city", "region"),
                    district=_first(
                        details,
                        "town",
                        "city_district",
                        "district",
                        "localadmin",
                        "county",
                    ),
                    neighborhood=_first(
                        details,
                        "neighbourhood",
                        "locality",
                        "suburb",
                        "quarter",
                        "village",
                    ),
                    street=roads.street,
                    avenue=roads.avenue,
                    result_type=normalize_text(
                        geocoding.get("type")
                        or item.get("addresstype")
                        or item.get("type")
                    ),
                    query_level=query_level,
                )
            )
        return candidates

    def _wait_for_rate_limit(self) -> None:
        with self._rate_lock:
            now = self.clock()
            wait_seconds = self.minimum_interval_seconds - (
                now - type(self)._last_request_time
            )
            if wait_seconds > 0:
                self.sleeper(wait_seconds)
            type(self)._last_request_time = self.clock()


def _first(values: dict[object, object], *keys: str) -> str | None:
    for key in keys:
        value = normalize_text(values.get(key))
        if value is not None:
            return value
    return None


def _area_contains(area: dict[str, object], latitude: float, longitude: float) -> bool:
    geometry = area.get("geojson")
    if isinstance(geometry, dict) and geometry.get("type") in {"Polygon", "MultiPolygon"}:
        return _geometry_contains(geometry, latitude, longitude)
    boundingbox = area.get("boundingbox")
    if not isinstance(boundingbox, list) or len(boundingbox) < 4:
        return False
    try:
        south, north, west, east = (float(value) for value in boundingbox[:4])
    except (TypeError, ValueError):
        return False
    return south <= latitude <= north and west <= longitude <= east


def _geometry_contains(
    geometry: dict[str, object],
    latitude: float,
    longitude: float,
) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, list):
        return _polygon_contains(coordinates, latitude, longitude)
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        return any(
            _polygon_contains(polygon, latitude, longitude)
            for polygon in coordinates
            if isinstance(polygon, list)
        )
    return False


def _polygon_contains(
    polygon: list[object],
    latitude: float,
    longitude: float,
) -> bool:
    if not polygon or not isinstance(polygon[0], list):
        return False
    if not _ring_contains(polygon[0], latitude, longitude):
        return False
    return not any(
        _ring_contains(hole, latitude, longitude)
        for hole in polygon[1:]
        if isinstance(hole, list)
    )


def _ring_contains(ring: list[object], latitude: float, longitude: float) -> bool:
    points: list[tuple[float, float]] = []
    for point in ring:
        if not isinstance(point, list) or len(point) < 2:
            continue
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    if len(points) < 3:
        return False
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        crosses = (y1 > latitude) != (y2 > latitude)
        if crosses:
            crossing_x = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < crossing_x:
                inside = not inside
        previous = current
    return inside
