"""Aşama 11 kayıtlı rota tasarımı ve kontrollü silme testleri."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from database.connection import create_database_engine
from database.models import (
    Atama,
    AtamaDurumu,
    Base,
    Personel,
    Rota,
    RotaKontrolNoktasi,
    Servis,
    ServisVardiyasi,
    ServisVardiyaRota,
    VardiyaTuru,
    YakaTuru,
)
from services.geocoding import GeocodingCandidate, GeocodingSearchResponse
from services.route_design import (
    NewServiceForRoute,
    RouteDesignError,
    RouteDesignPoint,
    RouteDesignService,
    RouteSaveRequest,
)
from services.route_design_rendering import render_route_designer_html
from services.routing import (
    MatchedTraceResult,
    OsrmRoutingService,
    RouteCoordinate,
    RoutingError,
    RoutingResult,
)
from services.service_management import ServiceManagementService
from ui.route_design_widget import RouteDesignWidget


class StubRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[RouteCoordinate, ...]] = []
        self.match_calls: list[tuple[tuple[RouteCoordinate, ...], float]] = []

    def match_trace(
        self,
        coordinates: tuple[RouteCoordinate, ...],
        *,
        radius_meters: float,
    ) -> MatchedTraceResult:
        self.match_calls.append((coordinates, radius_meters))
        snapped = tuple(
            RouteCoordinate(item.latitude + 0.00001, item.longitude + 0.00001)
            for item in coordinates
        )
        return MatchedTraceResult(
            snapped_coordinates=snapped,
            route=RoutingResult(
                distance_meters=1000.0,
                duration_seconds=120.0,
                geometry={
                    "type": "LineString",
                    "coordinates": [
                        [item.longitude, item.latitude] for item in snapped
                    ],
                },
            ),
        )

    def calculate(self, coordinates: tuple[RouteCoordinate, ...]) -> RoutingResult:
        self.calls.append(coordinates)
        return RoutingResult(
            distance_meters=12_345.0 + len(self.calls),
            duration_seconds=2_400.0,
            geometry={
                "type": "LineString",
                "coordinates": [
                    [point.longitude, point.latitude] for point in coordinates
                ],
            },
        )


class StubGeocoder:
    def __init__(self) -> None:
        self.calls = []

    def search(self, address):
        self.calls.append(address)
        index = len(self.calls)
        return GeocodingSearchResponse(
            candidates=(
                GeocodingCandidate(
                    place_id=str(index),
                    display_name=address.full_query(),
                    latitude=36.70 + index / 100,
                    longitude=34.50 + index / 100,
                    city=address.city,
                    district=address.district,
                    neighborhood=address.neighborhood,
                    street=address.street,
                    avenue=address.avenue,
                ),
            ),
            used_fallback=False,
        )


class StubHttpClient:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.payload


class FailingRouteDesignService(RouteDesignService):
    def _after_route_saved(self, session: Session, route: Rota) -> None:
        raise RuntimeError("Test transaction hatası")


def point(name: str, latitude: float, longitude: float) -> RouteDesignPoint:
    return RouteDesignPoint(
        name=name,
        address=f"{name} açık adresi",
        neighborhood="YENİ MAHALLESİ",
        street="33192. SOKAK",
        district="MEZİTLİ",
        city="MERSİN",
        latitude=latitude,
        longitude=longitude,
    )


class RouteDesignTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(Path(self.temp.name) / "stage11.db")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )
        self.router = StubRouter()
        self.geocoder = StubGeocoder()
        self.service = RouteDesignService(
            self.session_factory,
            router=self.router,
            geocoder=self.geocoder,
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def _seed_shift(
        self,
        code: str = "S_001",
        shift: VardiyaTuru = VardiyaTuru.SABAH,
        collar: YakaTuru = YakaTuru.MAVI,
    ) -> int:
        with self.session_factory() as session:
            service = session.scalar(select(Servis).where(Servis.servis_kodu == code))
            if service is None:
                service = Servis(servis_kodu=code, aktif_mi=True)
                session.add(service)
                session.flush()
            service_shift = ServisVardiyasi(
                servis_id=service.id,
                vardiya_turu=shift,
                yaka_turu=collar,
                kapasite=29,
                aktif_mi=True,
            )
            session.add(service_shift)
            session.commit()
            return service_shift.id

    def _calculation(self, suffix: str = ""):
        return self.service.calculate_manual(
            (point("Başlangıç" + suffix, 36.80, 34.60), point("Ara", 36.85, 34.70))
        )

    def _save(self, shift_ids: tuple[int, ...], name: str = "Mezitli Rotası"):
        return self.service.save_route(
            RouteSaveRequest(
                route_name=name,
                creation_method="MANUEL",
                calculation=self._calculation(),
                shift_ids=shift_ids,
            )
        )

    def _add_assignment(self, shift_id: int) -> int:
        with self.session_factory() as session:
            personnel = Personel(
                sicil_no="P000001",
                ad_soyad="Test Personel",
                acik_adres="Test Mahallesi, Mersin",
                vardiya_turu=VardiyaTuru.SABAH,
                yaka_turu=YakaTuru.MAVI,
                personel_turu="KADROLU",
                aktif_mi=True,
            )
            session.add(personnel)
            session.flush()
            assignment = Atama(
                personel_id=personnel.id,
                servis_vardiya_id=shift_id,
                atama_durumu=AtamaDurumu.ATANDI,
                aktif_mi=False,
            )
            session.add(assignment)
            session.commit()
            return assignment.id

    def _count(self, model) -> int:
        with self.session_factory() as session:
            return int(session.scalar(select(func.count()).select_from(model)) or 0)

    def test_manual_map_points_are_routed_in_order_and_factory_is_last(self) -> None:
        calculation = self._calculation()
        sent = self.router.calls[-1]
        self.assertEqual((sent[0].latitude, sent[1].latitude), (36.80, 36.85))
        self.assertEqual(sent[-1].latitude, calculation.destination.latitude)
        html = render_route_designer_html(
            calculation.points, calculation.destination, calculation.geometry
        )
        self.assertIn("draggable:true", html)
        self.assertIn("map.on('click'", html)
        self.assertIn("removePoint", html)
        self.assertIn("marker.on('contextmenu'", html)
        self.assertIn("map.on('mousedown'", html)
        self.assertIn("sampledDrawingPoints", html)
        self.assertIn("qrc:///qtwebchannel/qwebchannel.js", html)
        self.assertIn("routeBridge.traceDrawn", html)
        self.assertIn("routeBridge.pointsChanged", html)
        self.assertIn("marker.on('dragend'", html)
        self.assertIn("endMarker.bringToFront()", html)
        self.assertIn("undoLast", html)
        self.assertIn("clearPoints", html)

        empty_html = render_route_designer_html(
            (), calculation.destination, None
        )
        self.assertIn(
            "if(!markers.length&&!routeLine){map.setView([36.80,34.63],9);return;}",
            empty_html,
        )

    def test_drawn_trace_is_snapped_to_roads_before_factory_route(self) -> None:
        original = (point("Çizim 1", 36.80, 34.60), point("Çizim 2", 36.81, 34.61))
        calculation = self.service.calculate_drawn_trace(original)
        self.assertEqual(len(self.router.match_calls), 1)
        self.assertEqual(self.router.match_calls[0][1], 25.0)
        self.assertAlmostEqual(calculation.points[0].latitude, 36.80001)
        self.assertEqual(
            calculation.points[0].coordinate_status,
            "OSRM_YOL_ESLESMESI",
        )
        self.assertEqual(len(self.router.calls[-1]), 3)
        self.assertEqual(
            self.router.calls[-1][-1].latitude,
            calculation.destination.latitude,
        )

    def test_osrm_match_rejects_a_trace_point_without_nearby_road(self) -> None:
        client = StubHttpClient(
            {
                "code": "Ok",
                "tracepoints": [
                    {"location": [34.60, 36.80]},
                    None,
                ],
                "matchings": [],
            }
        )
        router = OsrmRoutingService(http_client=client)
        with self.assertRaisesRegex(RoutingError, "45 metre içinde"):
            router.match_trace(
                (RouteCoordinate(36.80, 34.60), RouteCoordinate(36.81, 34.61)),
                radius_meters=45.0,
            )
        _, request = client.calls[0]
        self.assertEqual(request["params"]["radiuses"], "45.0;45.0")
        self.assertEqual(request["params"]["gaps"], "split")

    def test_route_design_ui_keeps_a_large_map_and_groups_controls_in_tabs(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = RouteDesignWidget(
            service=self.service,
            service_management=ServiceManagementService(self.session_factory),
        )
        try:
            self.assertGreaterEqual(widget.map_view.minimumWidth(), 620)
            self.assertGreaterEqual(widget.map_view.minimumHeight(), 460)
            self.assertEqual(widget.control_tabs.count(), 3)
            self.assertEqual(
                [widget.control_tabs.tabText(index) for index in range(3)],
                ["Rota Ayarları", "Kontrol Noktaları", "Kayıtlı Rotalar"],
            )
            self.assertTrue(widget.point_entry_group.isHidden())
            self.assertTrue(widget.add_mode_button.isChecked())
            self.assertEqual(widget.map_view.minimumHeight(), 460)
            widget.method_combo.setCurrentIndex(1)
            self.assertFalse(widget.point_entry_group.isHidden())
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_json_map_sync_transfers_clicked_points_to_python(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = RouteDesignWidget(
            service=self.service,
            service_management=ServiceManagementService(self.session_factory),
        )
        try:
            widget._map_points_received(
                json.dumps(
                    {
                        "ready": True,
                        "points": [
                            {
                                "latitude": 36.80,
                                "longitude": 34.60,
                                "name": "Harita noktası 1",
                                "sourceIndex": None,
                            }
                        ],
                    }
                )
            )
            self.assertEqual(len(widget._points), 1)
            self.assertEqual(widget._points[0].latitude, 36.80)
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_map_move_or_delete_marks_route_stale_and_enables_recalculation(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = RouteDesignWidget(
            service=self.service,
            service_management=ServiceManagementService(self.session_factory),
        )
        try:
            calculation = self._calculation()
            widget._route_calculated(calculation)
            widget._set_busy(False)
            self.assertTrue(widget.save_button.isEnabled())
            self.assertFalse(widget.calculate_button.isEnabled())

            widget._map_points_changed(
                json.dumps(
                    [
                        {
                            "latitude": 36.801,
                            "longitude": 34.601,
                            "name": "Taşınan nokta",
                            "sourceIndex": 0,
                        }
                    ]
                )
            )
            self.assertIsNone(widget._calculation)
            self.assertTrue(widget.calculate_button.isEnabled())
            self.assertFalse(widget.save_button.isEnabled())
            self.assertEqual(widget.calculate_button.text(), "Rotayı Yeniden Hesapla")
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_async_route_result_unlocks_save_before_result_callback_completes(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = RouteDesignWidget(
            service=self.service,
            service_management=ServiceManagementService(self.session_factory),
        )
        loop = QEventLoop()
        observed = {}
        try:
            calculation = self._calculation()
            widget._points = calculation.points
            widget._refresh_points()

            def completed(result) -> None:
                widget._route_calculated(result)
                observed["busy"] = widget._operation_in_progress
                observed["save_enabled"] = widget.save_button.isEnabled()
                loop.quit()

            self.assertTrue(widget._run_async(lambda: calculation, completed))
            QTimer.singleShot(3_000, loop.quit)
            loop.exec()
            self.assertEqual(
                observed,
                {"busy": False, "save_enabled": True},
            )
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_selected_manual_point_can_be_given_a_distinct_name(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = RouteDesignWidget(
            service=self.service,
            service_management=ServiceManagementService(self.session_factory),
        )
        try:
            widget._points = (point("Harita noktası", 36.80, 34.60),)
            widget._refresh_points()
            widget.point_table.selectRow(0)
            widget.point_name_edit.setText("Mezitli Toplanma Noktası")
            widget._rename_selected_point()
            self.assertEqual(widget._points[0].name, "Mezitli Toplanma Noktası")
            self.assertEqual(
                widget.point_table.item(0, 1).text(),
                "Mezitli Toplanma Noktası",
            )
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_address_points_are_geocoded_and_routed_in_given_order(self) -> None:
        inputs = (
            RouteDesignPoint(
                name="Bir",
                neighborhood="ANAYURT MAHALLESİ",
                street="33192. SOKAK",
                district="MEZİTLİ",
                city="MERSİN",
            ),
            RouteDesignPoint(
                name="İki",
                avenue="GMK BULVARI",
                district="YENİŞEHİR",
                city="MERSİN",
            ),
        )
        result = self.service.calculate_automatic(inputs)
        self.assertEqual(len(self.geocoder.calls), 2)
        self.assertEqual([item.name for item in result.points], ["Bir", "İki"])
        self.assertEqual(len(self.router.calls[-1]), 3)

    def test_point_add_update_delete_and_reorder(self) -> None:
        points = (point("A", 36.7, 34.5), point("B", 36.8, 34.6))
        points = self.service.insert_point(points, 1, point("Araya", 36.75, 34.55))
        self.assertEqual([item.name for item in points], ["A", "Araya", "B"])
        points = self.service.update_point(points, 1, point("Güncel", 36.76, 34.56))
        points = self.service.move_point(points, 1, -1)
        self.assertEqual(points[0].name, "Güncel")
        points = self.service.delete_point(points, 0)
        self.assertEqual([item.name for item in points], ["A", "B"])

    def test_route_recalculation_uses_changed_coordinates(self) -> None:
        first = self._calculation()
        changed = self.service.update_point(
            first.points, 0, point("Taşındı", 36.91, 34.81)
        )
        second = self.service.recalculate(changed)
        self.assertEqual(self.router.calls[-1][0].latitude, 36.91)
        self.assertNotEqual(first.distance_meters, second.distance_meters)

    def test_route_is_saved_with_geometry_points_and_shift_metadata(self) -> None:
        shift_id = self._seed_shift()
        saved = self._save((shift_id,))
        self.assertEqual(saved.route_name, "Mezitli Rotası")
        self.assertEqual(saved.shift_ids, (shift_id,))
        self.assertEqual(len(saved.points), 2)
        self.assertEqual(saved.geometry["type"], "LineString")
        self.assertEqual(self._count(RotaKontrolNoktasi), 2)
        self.assertEqual(self._count(ServisVardiyaRota), 1)
        with self.session_factory() as session:
            link = session.scalar(select(ServisVardiyaRota))
            self.assertEqual(link.kapasite, 29)
            self.assertEqual(link.yaka_turu, YakaTuru.MAVI.value)

    def test_new_service_shift_route_and_points_are_one_transaction(self) -> None:
        with self.session_factory() as session:
            session.add(Servis(servis_kodu="S_016", aktif_mi=True))
            session.commit()
        self.assertEqual(self.service.suggest_service_code(), "S_017")
        saved = self.service.save_route(
            RouteSaveRequest(
                route_name="Yeni Servis Rotası",
                creation_method="MANUEL",
                calculation=self._calculation(),
                new_service=NewServiceForRoute(
                    service_code="S_017",
                    shift="08:00-17:30",
                    collar="BEYAZ",
                    capacity=16,
                    service_code_confirmed=True,
                ),
            )
        )
        self.assertEqual(len(saved.shift_ids), 1)
        self.assertEqual(self._count(Servis), 2)
        self.assertEqual(self._count(Rota), 1)

    def test_unconfirmed_service_code_is_rejected(self) -> None:
        with self.assertRaisesRegex(RouteDesignError, "onaylanmalıdır"):
            self.service.save_route(
                RouteSaveRequest(
                    route_name="Onaysız",
                    creation_method="MANUEL",
                    calculation=self._calculation(),
                    new_service=NewServiceForRoute(
                        "S_001", "08:00-17:30", "BEYAZ", 16
                    ),
                )
            )
        self.assertEqual(self._count(Servis), 0)

    def test_transaction_failure_rolls_back_service_shift_route_and_points(self) -> None:
        failing = FailingRouteDesignService(
            self.session_factory, router=self.router, geocoder=self.geocoder
        )
        with self.assertRaises(RouteDesignError):
            failing.save_route(
                RouteSaveRequest(
                    route_name="Geri Al",
                    creation_method="MANUEL",
                    calculation=self._calculation(),
                    new_service=NewServiceForRoute(
                        "S_001", "08:00-17:30", "BEYAZ", 16, True, True
                    ),
                )
            )
        self.assertEqual(self._count(Servis), 0)
        self.assertEqual(self._count(ServisVardiyasi), 0)
        self.assertEqual(self._count(Rota), 0)
        self.assertEqual(self._count(RotaKontrolNoktasi), 0)

    def test_same_service_shifts_can_use_different_routes_and_route_can_be_shared(self) -> None:
        first_shift = self._seed_shift()
        second_shift = self._seed_shift(
            shift=VardiyaTuru.AKSAM, collar=YakaTuru.MAVI
        )
        first = self._save((first_shift,), "Sabah Rotası")
        second = self._save((second_shift,), "Akşam Rotası")
        shared = self._save((first_shift, second_shift), "Ortak Rota")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(set(shared.shift_ids), {first_shift, second_shift})

    def test_saved_route_can_be_updated(self) -> None:
        shift_id = self._seed_shift()
        saved = self._save((shift_id,))
        changed = self.service.recalculate((point("Yeni Başlangıç", 36.9, 34.8),))
        updated = self.service.save_route(
            RouteSaveRequest(
                route_name="Güncellenmiş Rota",
                creation_method="OTOMATIK",
                calculation=changed,
                shift_ids=(shift_id,),
                active=False,
            ),
            route_id=saved.id,
        )
        self.assertEqual(updated.id, saved.id)
        self.assertEqual(updated.route_name, "Güncellenmiş Rota")
        self.assertFalse(updated.active)
        self.assertEqual(len(updated.points), 1)

    def test_route_can_be_activated_deactivated_and_unused_route_deleted(self) -> None:
        saved = self._save((self._seed_shift(),))
        self.assertFalse(self.service.set_route_active(saved.id, False).active)
        self.assertTrue(self.service.set_route_active(saved.id, True).active)
        result = self.service.delete_route_controlled(saved.id)
        self.assertTrue(result.physical_deleted)
        self.assertEqual(self._count(Rota), 0)

    def test_route_with_assignment_history_is_only_deactivated(self) -> None:
        shift_id = self._seed_shift()
        saved = self._save((shift_id,))
        assignment_id = self._add_assignment(shift_id)
        result = self.service.delete_route_controlled(saved.id)
        self.assertFalse(result.physical_deleted)
        self.assertTrue(result.deactivated)
        self.assertFalse(self.service.get_route(saved.id).active)
        self.assertEqual(self._count(Atama), 1)
        self.assertGreater(assignment_id, 0)

    def test_removed_shift_link_keeps_history_and_still_prevents_route_deletion(self) -> None:
        first_shift = self._seed_shift()
        second_shift = self._seed_shift(
            shift=VardiyaTuru.AKSAM, collar=YakaTuru.MAVI
        )
        saved = self._save((first_shift,))
        self._add_assignment(first_shift)
        updated = self.service.save_route(
            RouteSaveRequest(
                route_name="Bağlantısı Güncel",
                creation_method="MANUEL",
                calculation=self._calculation(),
                shift_ids=(second_shift,),
            ),
            route_id=saved.id,
        )
        self.assertEqual(updated.shift_ids, (second_shift,))
        result = self.service.delete_route_controlled(saved.id)
        self.assertTrue(result.deactivated)
        self.assertEqual(self._count(Rota), 1)

    def test_unused_service_is_deleted_but_historical_service_is_deactivated(self) -> None:
        unused_shift = self._seed_shift("S_001")
        with self.session_factory() as session:
            unused_service_id = session.get(ServisVardiyasi, unused_shift).servis_id
        manager = ServiceManagementService(self.session_factory)
        unused = manager.delete_service_controlled(unused_service_id)
        self.assertTrue(unused.physical_deleted)

        used_shift = self._seed_shift("S_002")
        self._add_assignment(used_shift)
        with self.session_factory() as session:
            used_service_id = session.get(ServisVardiyasi, used_shift).servis_id
        used = manager.delete_service_controlled(used_service_id)
        self.assertTrue(used.deactivated)
        with self.session_factory() as session:
            service = session.get(Servis, used_service_id)
            shift = session.get(ServisVardiyasi, used_shift)
            self.assertFalse(service.aktif_mi)
            self.assertFalse(shift.aktif_mi)

    def test_stage11_schema_exists_without_changing_assignment_rows(self) -> None:
        shift_id = self._seed_shift()
        assignment_id = self._add_assignment(shift_id)
        self._save((shift_id,))
        tables = set(inspect(self.engine).get_table_names())
        self.assertTrue(
            {"rota", "rota_kontrol_noktasi", "servis_vardiya_rota"}.issubset(tables)
        )
        with self.session_factory() as session:
            assignment = session.get(Atama, assignment_id)
            self.assertEqual(assignment.servis_vardiya_id, shift_id)
            self.assertFalse(assignment.aktif_mi)


if __name__ == "__main__":
    unittest.main()
