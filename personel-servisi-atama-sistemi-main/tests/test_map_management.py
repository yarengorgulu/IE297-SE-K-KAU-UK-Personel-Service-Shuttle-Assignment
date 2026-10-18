"""Aşama 10 adres çözümleme, harita ve rota hesaplama testleri."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtWidgets import QApplication
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from database.connection import create_database_engine
from database.init_db import initialize_database
from database.models import (
    Atama,
    AtamaDurumu,
    Personel,
    PersonelTuru,
    Servis,
    ServisGuzergahGeometrisi,
    ServisGuzergahNoktasi,
    ServisVardiyasi,
    VardiyaTuru,
    YakaTuru,
)
from database.repositories import RoutePointRepository
from services import (
    AddressInput,
    CoordinateManagementService,
    CoordinateValidationError,
    ExternalServiceTimeoutError,
    GeocodingCandidate,
    GeocodingSearchResponse,
    MapManagementError,
    NominatimGeocodingService,
    OptimizedRoutingResult,
    OsrmRoutingService,
    PersonnelFormData,
    PersonnelManagementService,
    RouteCalculationError,
    RouteCalculationService,
    RouteCoordinate,
    RouteManagementService,
    RoutePointFormData,
    RoutingResult,
    assess_candidate,
    compare_address,
    optimize_fixed_destination_order,
)
from services.map_rendering import render_map_html
from ui.map_management_widget import MapManagementWidget


class QueueHttpClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_json(self, url, *, params, headers, timeout):
        self.calls.append((url, dict(params)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StubGeocoder:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def search(self, address: AddressInput) -> GeocodingSearchResponse:
        key = address.address or address.neighborhood or ""
        self.calls.append(key)
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        return response


class StubRouter:
    def __init__(
        self,
        *,
        fail: Exception | None = None,
        pickup_order: tuple[int, ...] | None = None,
    ) -> None:
        self.fail = fail
        self.calls: list[tuple[object, ...]] = []
        self.pickup_order = pickup_order

    def calculate_optimized_to_destination(self, pickups, destination):
        if self.fail is not None:
            raise self.fail
        order = self.pickup_order or tuple(range(len(pickups)))
        route = self.calculate(
            tuple(pickups[index] for index in order) + (destination,)
        )
        return OptimizedRoutingResult(route=route, pickup_order=order)

    def calculate(self, coordinates):
        self.calls.append(tuple(coordinates))
        return RoutingResult(
            distance_meters=12_500.0,
            duration_seconds=1_800.0,
            geometry={
                "type": "LineString",
                "coordinates": [
                    [point.longitude, point.latitude] for point in coordinates
                ],
            },
        )


def candidate(
    name: str = "Çiftlik Mahallesi, Yenişehir, Mersin",
    *,
    latitude: float = 36.80,
    longitude: float = 34.60,
    city: str = "MERSİN",
    district: str = "YENİŞEHİR",
    neighborhood: str = "ÇİFTLİK",
    street: str | None = None,
    avenue: str | None = None,
) -> GeocodingCandidate:
    return GeocodingCandidate(
        place_id=name,
        display_name=name,
        latitude=latitude,
        longitude=longitude,
        city=city,
        district=district,
        neighborhood=neighborhood,
        street=street,
        avenue=avenue,
    )


class MapManagementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            Path(self.temporary_directory.name) / "map-test.db"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _seed_personnel(
        self,
        number: str = "P000001",
        *,
        address: str = "Test Sokak No: 1",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> int:
        with self.session_factory() as session:
            personnel = Personel(
                sicil_no=number,
                ad="Ayşe",
                soyad="Yılmaz",
                ad_soyad="Ayşe Yılmaz",
                acik_adres=address,
                il="MERSİN",
                ilce="YENİŞEHİR",
                mahalle="ÇİFTLİK",
                enlem=latitude,
                boylam=longitude,
                koordinat_durumu=(
                    "MEVCUT" if latitude is not None and longitude is not None else "EKSIK"
                ),
                vardiya_turu=VardiyaTuru.SABAH,
                yaka_turu=YakaTuru.MAVI,
                personel_turu=PersonelTuru.KADROLU,
            )
            session.add(personnel)
            session.commit()
            return personnel.id

    def _seed_route(
        self,
        code: str = "S_001",
        *,
        shift: VardiyaTuru = VardiyaTuru.SABAH,
        first_district: str = "YENİŞEHİR",
        second_district: str = "MEZİTLİ",
    ) -> tuple[int, tuple[int, int]]:
        with self.session_factory() as session:
            service = Servis(servis_kodu=code)
            service_shift = ServisVardiyasi(
                vardiya_turu=shift,
                yaka_turu=YakaTuru.MAVI,
                kapasite=29,
                rota_adi="MERSİN",
            )
            service.vardiyalar.append(service_shift)
            service_shift.guzergah_noktalari.extend(
                [
                    ServisGuzergahNoktasi(
                        sira_no=1,
                        il="MERSİN",
                        ilce=first_district,
                        mahalle="ÇİFTLİK",
                        enlem=36.80,
                        boylam=34.60,
                        koordinat_durumu="MEVCUT",
                    ),
                    ServisGuzergahNoktasi(
                        sira_no=2,
                        il="MERSİN",
                        ilce=second_district,
                        mahalle="TECE",
                        enlem=36.75,
                        boylam=34.55,
                        koordinat_durumu="MEVCUT",
                    ),
                ]
            )
            session.add(service)
            session.commit()
            return service_shift.id, tuple(
                point.id for point in service_shift.guzergah_noktalari
            )

    def test_valid_address_can_be_resolved_and_confirmed(self) -> None:
        personnel_id = self._seed_personnel()
        geocoder = StubGeocoder(
            {"Test Sokak No: 1": GeocodingSearchResponse((candidate(),), False)}
        )
        service = CoordinateManagementService(
            self.session_factory,
            geocoder=geocoder,
        )
        lookup = service.search_personnel(personnel_id)
        self.assertEqual(len(lookup.candidates), 1)
        updated = service.confirm_personnel_candidate(
            personnel_id,
            lookup.candidates[0].candidate,
        )
        self.assertEqual((updated.latitude, updated.longitude), (36.8, 34.6))
        self.assertEqual(updated.coordinate_status, "COZUMLENDI")

    def test_not_found_keeps_record_and_marks_status(self) -> None:
        personnel_id = self._seed_personnel()
        service = CoordinateManagementService(
            self.session_factory,
            geocoder=StubGeocoder(
                {"Test Sokak No: 1": GeocodingSearchResponse((), True)}
            ),
        )
        lookup = service.search_personnel(personnel_id)
        self.assertFalse(lookup.candidates)
        target = service.get_personnel_target(personnel_id)
        self.assertEqual(target.address, "Test Sokak No: 1")
        self.assertIsNone(target.latitude)
        self.assertEqual(target.coordinate_status, "BULUNAMADI")

    def test_multiple_geocoding_results_are_returned_for_user_selection(self) -> None:
        personnel_id = self._seed_personnel()
        responses = (candidate("Birinci"), candidate("İkinci", latitude=36.81))
        service = CoordinateManagementService(
            self.session_factory,
            geocoder=StubGeocoder(
                {"Test Sokak No: 1": GeocodingSearchResponse(responses, False)}
            ),
        )
        lookup = service.search_personnel(personnel_id)
        self.assertEqual(len(lookup.candidates), 2)
        self.assertIsNone(service.get_personnel_target(personnel_id).latitude)

    def test_manual_coordinates_are_validated(self) -> None:
        personnel_id = self._seed_personnel()
        service = CoordinateManagementService(self.session_factory)
        with self.assertRaises(CoordinateValidationError):
            service.set_personnel_coordinates(personnel_id, 91, 34.6)
        updated = service.set_personnel_coordinates(personnel_id, "36,8", "34,6")
        self.assertEqual(updated.coordinate_status, "MANUEL")

    def test_city_district_and_neighborhood_mismatch_is_detected(self) -> None:
        mismatches = compare_address(
            AddressInput("Adres", "MERSİN", "YENİŞEHİR", "ÇİFTLİK"),
            candidate(city="ADANA", district="SEYHAN", neighborhood="KURTULUŞ"),
        )
        self.assertEqual({item.field for item in mismatches}, {"İl", "İlçe", "Mahalle"})

    def test_street_and_avenue_mismatch_is_detected_without_silent_change(self) -> None:
        mismatches = compare_address(
            AddressInput(
                "Adres",
                "MERSİN",
                "MEZİTLİ",
                "YENİ MAHALLE",
                street="33192. Sokak",
                avenue="Atatürk Caddesi",
            ),
            candidate(
                city="MERSİN",
                district="MEZİTLİ",
                neighborhood="Yeni Mahallesi",
                street="33193. Sokak",
                avenue=None,
            ),
        )
        self.assertEqual({item.field for item in mismatches}, {"Sokak", "Cadde"})

    def test_address_queries_are_detailed_to_general_and_do_not_repeat_parts(self) -> None:
        address = AddressInput(
            "Arya Sitesi Kat:5 No:9",
            "Mersin",
            "Mezitli",
            "Yeni Mahalle",
            street="33192. Sokak",
            avenue="Gazi Mustafa Kemal Bulvarı",
        )
        queries = address.controlled_queries()
        self.assertEqual(len(queries), 4)
        self.assertIn("Arya Sitesi", queries[0])
        self.assertIn("33192. Sokak", queries[0])
        self.assertNotIn("Arya Sitesi", queries[1])
        self.assertNotIn("33192. Sokak", queries[2])
        self.assertNotIn("Yeni Mahalle", queries[3])
        duplicate_safe = AddressInput(
            "33192. Sokak, Yeni Mahalle, Mezitli, Mersin",
            "Mersin",
            "Mezitli",
            "Yeni Mahalle",
            street="33192. Sokak",
        ).full_query()
        self.assertEqual(duplicate_safe.casefold().count("33192"), 1)
        self.assertEqual(duplicate_safe.casefold().count("yeni mahalle"), 1)

    def test_existing_coordinates_are_not_queried_without_force(self) -> None:
        personnel_id = self._seed_personnel(latitude=36.8, longitude=34.6)
        geocoder = StubGeocoder({})
        service = CoordinateManagementService(self.session_factory, geocoder=geocoder)
        result = service.search_personnel(personnel_id)
        self.assertTrue(result.skipped_existing)
        self.assertEqual(geocoder.calls, [])

    def test_nominatim_uses_full_address_then_controlled_fallback(self) -> None:
        http = QueueHttpClient(
            [
                [],
                [
                    {
                        "place_id": 1,
                        "display_name": "Çiftlik, Yenişehir, Mersin",
                        "lat": "36.8",
                        "lon": "34.6",
                        "address": {
                            "province": "Mersin",
                            "county": "Yenişehir",
                            "neighbourhood": "Çiftlik",
                        },
                    }
                ],
                [],
                [],
            ]
        )
        geocoder = NominatimGeocodingService(
            http_client=http,
            minimum_interval_seconds=0,
        )
        result = geocoder.search(
            AddressInput("Bilinmeyen Sokak", "Mersin", "Yenişehir", "Çiftlik")
        )
        self.assertTrue(result.used_fallback)
        self.assertEqual(result.queries_attempted, 3)
        self.assertEqual(len(http.calls), 4)
        self.assertIn("Bilinmeyen Sokak", str(http.calls[0][1]["q"]))
        self.assertNotIn("Bilinmeyen Sokak", str(http.calls[1][1]["q"]))
        self.assertEqual(http.calls[-1][0], geocoder.reverse_url)

    def test_nominatim_tries_extracted_street_before_neighborhood_fallback(self) -> None:
        http = QueueHttpClient(
            [
                [],
                [
                    {
                        "place_id": 2,
                        "display_name": "33192. Sokak, Yeni Mahallesi, Mezitli, Mersin",
                        "lat": "36.755",
                        "lon": "34.539",
                        "address": {
                            "province": "Mersin",
                            "town": "Mezitli",
                            "neighbourhood": "Yeni Mahallesi",
                        },
                    }
                ],
            ]
        )
        geocoder = NominatimGeocodingService(
            http_client=http,
            minimum_interval_seconds=0,
        )
        result = geocoder.search(
            AddressInput(
                "Yeni mahalle 33192. sokak Arya Mezitli 1 Sitesi Kat:5 No:9",
                "Mersin",
                "Mezitli",
                "Yeni mahalle",
            )
        )
        self.assertTrue(result.used_fallback)
        self.assertEqual(len(http.calls), 2)
        street_query = str(http.calls[1][1]["q"])
        self.assertIn("33192. Sokak", street_query)
        self.assertIn("Yeni mahalle", street_query)
        self.assertIn("Mezitli", street_query)
        self.assertNotIn("Sitesi", street_query)
        self.assertNotIn("Kat", street_query)

    def test_nominatim_result_exposes_separate_street_and_avenue_fields(self) -> None:
        http = QueueHttpClient(
            [
                [
                    {
                        "place_id": 3,
                        "display_name": "Atatürk Caddesi, Mezitli, Mersin",
                        "lat": "36.75",
                        "lon": "34.54",
                        "address": {
                            "province": "Mersin",
                            "town": "Mezitli",
                            "neighbourhood": "Yeni Mahallesi",
                            "road": "Atatürk Caddesi",
                        },
                    }
                ]
            ]
        )
        result = NominatimGeocodingService(
            http_client=http,
            minimum_interval_seconds=0,
        ).search(AddressInput("Atatürk Caddesi", "Mersin", "Mezitli", "Yeni"))
        self.assertIsNone(result.candidates[0].street)
        self.assertEqual(result.candidates[0].avenue, "Atatürk Caddesi")

    def test_missing_structured_neighborhood_can_match_display_name(self) -> None:
        address = AddressInput(
            None,
            "Mersin",
            "Mezitli",
            "Anayurt Mahallesi",
        )
        found = candidate(
            name="Anayurt Mahallesi, Mezitli, Mersin, Türkiye",
            city="Mersin",
            district="Mezitli",
            neighborhood=None,
        )
        assessment = assess_candidate(address, found)
        self.assertFalse(assessment.mismatches)
        self.assertIn("Mahalle", assessment.details[0])
        self.assertEqual(assessment.status, "UYUMLU")

    def test_reverse_lookup_fills_missing_neighborhood_and_verifies_candidate(self) -> None:
        search_candidate = {
            "place_id": 10,
            "display_name": "33192. Sokak, Mezitli, Mersin",
            "lat": "36.755",
            "lon": "34.539",
            "addresstype": "house",
            "address": {
                "province": "Mersin",
                "town": "Mezitli",
                "road": "33192. Sokak",
            },
        }
        reverse_candidate = {
            "place_id": 11,
            "display_name": "33192. Sokak, Anayurt Mahallesi, Mezitli, Mersin",
            "lat": "36.755",
            "lon": "34.539",
            "addresstype": "house",
            "address": {
                "province": "Mersin",
                "town": "Mezitli",
                "neighbourhood": "Anayurt Mahallesi",
                "road": "33192. Sokak",
            },
        }
        http = QueueHttpClient([[search_candidate], [], [], reverse_candidate])
        address = AddressInput(
            "Site No: 1",
            "Mersin",
            "Mezitli",
            "Anayurt Mahallesi",
            street="33192. Sokak",
        )
        geocoder = NominatimGeocodingService(
            http_client=http,
            minimum_interval_seconds=0,
        )
        result = geocoder.search(address)
        best = result.candidates[0]
        assessment = assess_candidate(address, best)
        self.assertTrue(best.reverse_checked)
        self.assertEqual(best.neighborhood, "Anayurt Mahallesi")
        self.assertEqual(assessment.status, "DOĞRULANDI")
        self.assertEqual(http.calls[-1][0], geocoder.reverse_url)

    def test_neighborhood_polygon_verifies_coordinate_when_result_omits_field(self) -> None:
        search_candidate = {
            "place_id": 20,
            "display_name": "33192. Sokak, Mezitli, Mersin",
            "lat": "36.755",
            "lon": "34.539",
            "addresstype": "house",
            "address": {
                "province": "Mersin",
                "town": "Mezitli",
                "road": "33192. Sokak",
            },
        }
        reverse_candidate = {
            **search_candidate,
            "place_id": 21,
        }
        neighborhood_area = {
            "place_id": 22,
            "display_name": "Anayurt Mahallesi, Mezitli, Mersin",
            "lat": "36.755",
            "lon": "34.539",
            "boundingbox": ["36.74", "36.77", "34.52", "34.56"],
            "geojson": {
                "type": "Polygon",
                "coordinates": [[
                    [34.52, 36.74],
                    [34.56, 36.74],
                    [34.56, 36.77],
                    [34.52, 36.77],
                    [34.52, 36.74],
                ]],
            },
            "address": {"neighbourhood": "Anayurt Mahallesi"},
        }
        http = QueueHttpClient(
            [[search_candidate], [], [], reverse_candidate, [neighborhood_area]]
        )
        address = AddressInput(
            "Site No: 1",
            "Mersin",
            "Mezitli",
            "Anayurt Mahallesi",
            street="33192. Sokak",
        )
        result = NominatimGeocodingService(
            http_client=http,
            minimum_interval_seconds=0,
        ).search(address)
        best = result.candidates[0]
        assessment = assess_candidate(address, best)
        self.assertTrue(best.neighborhood_area_verified)
        self.assertEqual(assessment.status, "MAHALLE ALANINDA DOĞRULANDI")
        self.assertFalse(assessment.mismatches)

    def test_invalid_reverse_response_does_not_discard_search_candidates(self) -> None:
        search_candidate = {
            "place_id": 25,
            "display_name": "33192. Sokak, Mezitli, Mersin",
            "lat": "36.755",
            "lon": "34.539",
            "addresstype": "house",
            "address": {
                "province": "Mersin",
                "town": "Mezitli",
                "road": "33192. Sokak",
            },
        }
        http = QueueHttpClient(
            [[search_candidate], [], [], {"error": "Sonuç yok"}, []]
        )
        result = NominatimGeocodingService(
            http_client=http,
            minimum_interval_seconds=0,
        ).search(
            AddressInput(
                "Site No: 1",
                "Mersin",
                "Mezitli",
                "Anayurt Mahallesi",
                street="33192. Sokak",
            )
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.candidates[0].reverse_checked)

    def test_geocodejson_response_is_normalized_and_ranked(self) -> None:
        http = QueueHttpClient(
            [
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [34.539, 36.755],
                            },
                            "properties": {
                                "geocoding": {
                                    "place_id": 30,
                                    "type": "house",
                                    "label": "33192. Sokak, Anayurt, Mezitli, Mersin",
                                    "city": "Mersin",
                                    "district": "Mezitli",
                                    "locality": "Anayurt Mahallesi",
                                    "street": "33192. Sokak",
                                }
                            },
                        }
                    ],
                }
            ]
        )
        result = NominatimGeocodingService(
            http_client=http,
            minimum_interval_seconds=0,
        ).search(
            AddressInput(
                None,
                "Mersin",
                "Mezitli",
                "Anayurt Mahallesi",
                street="33192. Sokak",
            )
        )
        self.assertEqual(result.candidates[0].result_type, "house")
        self.assertEqual(result.candidates[0].neighborhood, "Anayurt Mahallesi")
        self.assertEqual(http.calls[0][1]["format"], "geocodejson")

    def test_route_points_are_optimized_and_factory_is_always_last(self) -> None:
        shift_id, _ = self._seed_route()
        router = StubRouter()
        service = RouteCalculationService(self.session_factory, router=router)
        result = service.calculate_shift(shift_id)
        sent = router.calls[0]
        self.assertEqual(
            [(item.latitude, item.longitude) for item in sent],
            [
                (36.8, 34.6),
                (36.75, 34.55),
                (36.94375678451021, 34.97910084152655),
            ],
        )
        self.assertEqual(result.distance_meters, 12_500)
        self.assertEqual(result.duration_seconds, 1_800)
        self.assertEqual(
            [point.sequence for point in result.map_data.points],
            [1, 2, 3],
        )
        self.assertTrue(result.map_data.points[-1].is_destination)
        self.assertEqual(result.map_data.points[-1].label, "Seçil Kauçuk")
        self.assertEqual(result.map_data.points[-1].position_label, "Bitiş / Fabrika")

    def test_optimized_pickup_order_is_persisted_instead_of_excel_order(self) -> None:
        shift_id, point_ids = self._seed_route()
        service = RouteCalculationService(
            self.session_factory,
            router=StubRouter(pickup_order=(1, 0)),
        )
        result = service.calculate_shift(shift_id)
        self.assertEqual(
            [point.neighborhood for point in result.map_data.points],
            ["TECE", "ÇİFTLİK", "YUNUSOĞLU MAHALLESİ"],
        )
        with self.session_factory() as session:
            stored = RoutePointRepository(session).list_for_shift(
                shift_id,
                include_inactive=False,
            )
            self.assertEqual([point.id for point in stored], [point_ids[1], point_ids[0]])
            self.assertEqual([point.sira_no for point in stored], [1, 2])

    def test_exact_optimizer_uses_free_start_and_fixed_destination(self) -> None:
        durations = (
            (0.0, 10.0, 1.0, 10.0),
            (1.0, 0.0, 10.0, 10.0),
            (10.0, 10.0, 0.0, 1.0),
            (10.0, 10.0, 10.0, 0.0),
        )
        self.assertEqual(
            optimize_fixed_destination_order(durations),
            (1, 0, 2),
        )

    def test_osrm_matrix_optimization_calls_route_with_factory_last(self) -> None:
        http = QueueHttpClient(
            [
                {
                    "code": "Ok",
                    "durations": [
                        [0, 10, 1],
                        [1, 0, 10],
                        [10, 10, 0],
                    ],
                },
                {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": 1000,
                            "duration": 120,
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [
                                    [34.55, 36.75],
                                    [34.6, 36.8],
                                    [34.97910084152655, 36.94375678451021],
                                ],
                            },
                        }
                    ],
                },
            ]
        )
        router = OsrmRoutingService(http_client=http)
        optimized = router.calculate_optimized_to_destination(
            (
                RouteCoordinate(latitude=36.8, longitude=34.6),
                RouteCoordinate(latitude=36.75, longitude=34.55),
            ),
            RouteCoordinate(
                latitude=36.94375678451021,
                longitude=34.97910084152655,
            ),
        )
        self.assertEqual(optimized.pickup_order, (1, 0))
        self.assertIn("/table/", http.calls[0][0])
        self.assertTrue(
            http.calls[1][0].endswith(
                "/34.55000000,36.75000000;34.60000000,36.80000000;"
                "34.97910084,36.94375678"
            )
        )

    def test_osrm_url_uses_longitude_latitude_in_given_order(self) -> None:
        http = QueueHttpClient(
            [
                {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": 1000,
                            "duration": 120,
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[34.6, 36.8], [34.55, 36.75]],
                            },
                        }
                    ],
                }
            ]
        )
        router = OsrmRoutingService(http_client=http)
        router.calculate(
            (
                RouteCoordinate(latitude=36.8, longitude=34.6),
                RouteCoordinate(latitude=36.75, longitude=34.55),
            )
        )
        self.assertTrue(
            http.calls[0][0].endswith(
                "/34.60000000,36.80000000;34.55000000,36.75000000"
            )
        )

    def test_one_route_can_use_multiple_districts_and_neighborhoods(self) -> None:
        shift_id, _ = self._seed_route()
        data = RouteCalculationService(self.session_factory).get_shift_map_data(shift_id)
        self.assertEqual(
            [
                (point.district, point.neighborhood)
                for point in data.points
                if not point.is_destination
            ],
            [("YENİŞEHİR", "ÇİFTLİK"), ("MEZİTLİ", "TECE")],
        )

    def test_factory_coordinate_change_marks_all_routes_stale(self) -> None:
        first_shift, _ = self._seed_route("S_001", shift=VardiyaTuru.SABAH)
        second_shift, _ = self._seed_route("S_002", shift=VardiyaTuru.AKSAM)
        service = RouteCalculationService(
            self.session_factory,
            router=StubRouter(),
        )
        service.calculate_shift(first_shift)
        service.calculate_shift(second_shift)
        service.set_factory_coordinates(36.946, 34.981)
        self.assertFalse(service.get_shift_map_data(first_shift).route_is_current)
        self.assertFalse(service.get_shift_map_data(second_shift).route_is_current)
        destination = service.get_factory_destination()
        self.assertEqual((destination.latitude, destination.longitude), (36.946, 34.981))

    def test_same_service_different_shifts_store_different_routes(self) -> None:
        first_shift, _ = self._seed_route("S_001", shift=VardiyaTuru.SABAH)
        with self.session_factory() as session:
            service = session.scalar(select(Servis).where(Servis.servis_kodu == "S_001"))
            second = ServisVardiyasi(
                vardiya_turu=VardiyaTuru.AKSAM,
                yaka_turu=YakaTuru.MAVI,
                kapasite=29,
            )
            second.guzergah_noktalari.extend(
                [
                    ServisGuzergahNoktasi(
                        sira_no=1, ilce="TARSUS", enlem=36.9, boylam=34.9
                    ),
                    ServisGuzergahNoktasi(
                        sira_no=2, ilce="AKDENİZ", enlem=36.85, boylam=34.7
                    ),
                ]
            )
            service.vardiyalar.append(second)
            session.commit()
            second_shift = second.id
        route_service = RouteCalculationService(
            self.session_factory,
            router=StubRouter(),
        )
        route_service.calculate_shift(first_shift)
        route_service.calculate_shift(second_shift)
        with self.session_factory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(ServisGuzergahGeometrisi)
                ),
                2,
            )

    def test_coordinate_change_marks_stored_route_stale(self) -> None:
        shift_id, point_ids = self._seed_route()
        route_service = RouteCalculationService(
            self.session_factory,
            router=StubRouter(),
        )
        self.assertTrue(route_service.calculate_shift(shift_id).map_data.route_is_current)
        CoordinateManagementService(self.session_factory).set_route_coordinates(
            point_ids[0],
            36.81,
            34.61,
        )
        self.assertFalse(route_service.get_shift_map_data(shift_id).route_is_current)

    def test_route_address_change_marks_coordinates_and_geometry_stale(self) -> None:
        shift_id, point_ids = self._seed_route()
        calculator = RouteCalculationService(
            self.session_factory,
            router=StubRouter(),
        )
        calculator.calculate_shift(shift_id)
        manager = RouteManagementService(self.session_factory)
        current = manager.get_point(point_ids[0])
        manager.update_point(
            current.id,
            RoutePointFormData(
                sequence=current.sequence,
                city=current.city,
                district=current.district,
                neighborhood=current.neighborhood,
                street="33192. Sokak",
                avenue=current.avenue,
                point_name=current.point_name,
                address=current.address,
                description=current.description,
                latitude=current.latitude,
                longitude=current.longitude,
                active=current.active,
            ),
        )
        updated = CoordinateManagementService(
            self.session_factory
        ).get_route_target(current.id)
        self.assertEqual(updated.coordinate_status, "GUNCEL_DEGIL")
        self.assertFalse(calculator.get_shift_map_data(shift_id).route_is_current)
        with self.assertRaises(RouteCalculationError):
            calculator.calculate_shift(shift_id)

    def test_route_calculation_failure_keeps_previous_result(self) -> None:
        shift_id, _ = self._seed_route()
        successful = RouteCalculationService(
            self.session_factory,
            router=StubRouter(),
        )
        successful.calculate_shift(shift_id)
        failing = RouteCalculationService(
            self.session_factory,
            router=StubRouter(fail=ExternalServiceTimeoutError("OSRM zaman aşımı")),
        )
        with self.assertRaises(RouteCalculationError):
            failing.calculate_shift(shift_id)
        data = successful.get_shift_map_data(shift_id)
        self.assertEqual(data.distance_meters, 12_500)
        self.assertTrue(data.route_is_current)

    def test_timeout_is_returned_as_safe_application_error(self) -> None:
        personnel_id = self._seed_personnel()
        service = CoordinateManagementService(
            self.session_factory,
            geocoder=StubGeocoder(
                {"Test Sokak No: 1": ExternalServiceTimeoutError("Adres servisi zaman aşımı")}
            ),
        )
        with self.assertRaisesRegex(MapManagementError, "zaman aşımı"):
            service.search_personnel(personnel_id)

    def test_bulk_failure_does_not_block_next_record(self) -> None:
        self._seed_personnel("P000001", address="FAIL")
        second_id = self._seed_personnel("P000002", address="OK")
        geocoder = StubGeocoder(
            {
                "FAIL": ExternalServiceTimeoutError("timeout"),
                "OK": GeocodingSearchResponse((candidate(),), False),
            }
        )
        service = CoordinateManagementService(self.session_factory, geocoder=geocoder)
        progress: list[tuple[int, int]] = []
        result = service.bulk_geocode_personnel(
            lambda current, total: progress.append((current, total)),
            lambda: False,
        )
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(service.get_personnel_target(second_id).coordinate_status, "COZUMLENDI")
        self.assertEqual(progress[-1], (2, 2))

    def test_bulk_operation_can_be_cancelled_between_records(self) -> None:
        self._seed_personnel("P000001", address="ONE")
        geocoder = StubGeocoder(
            {"ONE": GeocodingSearchResponse((candidate(),), False)}
        )
        service = CoordinateManagementService(self.session_factory, geocoder=geocoder)
        result = service.bulk_geocode_personnel(lambda current, total: None, lambda: True)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.success_count, 0)
        self.assertEqual(geocoder.calls, [])

    def test_personnel_address_change_marks_coordinates_stale(self) -> None:
        personnel_id = self._seed_personnel(latitude=36.8, longitude=34.6)
        management = PersonnelManagementService(self.session_factory)
        management.update_personnel(
            personnel_id,
            PersonnelFormData(
                personnel_number="P000001",
                first_name="Ayşe",
                last_name="Yılmaz",
                address="Yeni Adres No: 2",
                city="MERSİN",
                district="YENİŞEHİR",
                neighborhood="ÇİFTLİK",
                shift="08:00-16:00",
                collar="MAVİ",
            ),
        )
        target = CoordinateManagementService(self.session_factory).get_personnel_target(
            personnel_id
        )
        self.assertEqual(target.coordinate_status, "GUNCEL_DEGIL")
        self.assertEqual((target.latitude, target.longitude), (36.8, 34.6))

    def test_personnel_street_change_marks_coordinates_stale(self) -> None:
        personnel_id = self._seed_personnel(latitude=36.8, longitude=34.6)
        PersonnelManagementService(self.session_factory).update_personnel(
            personnel_id,
            PersonnelFormData(
                personnel_number="P000001",
                first_name="Ayşe",
                last_name="Yılmaz",
                address="Test Sokak No: 1",
                city="MERSİN",
                district="YENİŞEHİR",
                neighborhood="ÇİFTLİK",
                street="Yeni Sokak",
                shift="08:00-16:00",
                collar="MAVİ",
            ),
            allow_warnings=True,
        )
        target = CoordinateManagementService(self.session_factory).get_personnel_target(
            personnel_id
        )
        self.assertEqual(target.coordinate_status, "GUNCEL_DEGIL")

    def test_map_html_distinguishes_start_middle_end_and_attribution(self) -> None:
        shift_id, _ = self._seed_route()
        with self.session_factory() as session:
            session.add(
                ServisGuzergahNoktasi(
                    servis_vardiya_id=shift_id,
                    sira_no=3,
                    ilce="AKDENİZ",
                    mahalle="HAMİDİYE",
                    enlem=36.79,
                    boylam=34.62,
                )
            )
            session.commit()
        data = RouteCalculationService(self.session_factory).get_shift_map_data(shift_id)
        rendered = render_map_html(route=data)
        self.assertIn("Başlangıç", rendered)
        self.assertIn("Ara Nokta", rendered)
        self.assertIn("Bitiş", rendered)
        self.assertIn("OpenStreetMap katkıda bulunanlar", rendered)

    def test_second_ui_operation_is_rejected_while_first_is_running(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = MapManagementWidget(
            personnel_service=PersonnelManagementService(self.session_factory),
            coordinate_service=CoordinateManagementService(self.session_factory),
            route_service=RouteCalculationService(self.session_factory),
        )
        try:
            widget._operation_in_progress = True
            self.assertFalse(widget._run_async(lambda: None, lambda result: None))
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_map_operations_do_not_change_assignments_or_occupancy(self) -> None:
        personnel_id = self._seed_personnel()
        shift_id, _ = self._seed_route()
        with self.session_factory() as session:
            session.add(
                Atama(
                    personel_id=personnel_id,
                    servis_vardiya_id=shift_id,
                    atama_durumu=AtamaDurumu.ATANDI,
                    aktif_mi=True,
                )
            )
            session.commit()
        before = self._assignment_snapshot()
        CoordinateManagementService(self.session_factory).set_personnel_coordinates(
            personnel_id,
            36.8,
            34.6,
        )
        RouteCalculationService(
            self.session_factory,
            router=StubRouter(),
        ).calculate_shift(shift_id)
        self.assertEqual(self._assignment_snapshot(), before)

    def _assignment_snapshot(self) -> tuple[int, int, int]:
        with self.session_factory() as session:
            assignment_count = int(
                session.scalar(select(func.count()).select_from(Atama)) or 0
            )
            active_assignment_count = int(
                session.scalar(
                    select(func.count()).select_from(Atama).where(Atama.aktif_mi.is_(True))
                )
                or 0
            )
            capacity = int(session.scalar(select(ServisVardiyasi.kapasite)) or 0)
            return assignment_count, active_assignment_count, capacity


if __name__ == "__main__":
    unittest.main()
