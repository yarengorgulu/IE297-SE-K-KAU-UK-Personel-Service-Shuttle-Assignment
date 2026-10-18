"""Aşama 12 personel-rota uygunluğu ve atama önizleme testleri."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from PySide6.QtWidgets import QApplication

from database.connection import create_database_engine
from database.models import (
    Atama,
    AtamaDurumu,
    Base,
    Personel,
    PersonelTuru,
    Rota,
    Servis,
    ServisVardiyasi,
    ServisVardiyaRota,
    VardiyaTuru,
    YakaTuru,
)
from services.assignment_geometry import nearest_point_on_linestring
from services.assignment_preview import AssignmentPreviewService
from services.assignment_preview_rendering import render_assignment_preview_html
from services.service_management import ServiceManagementService
from ui.assignment_preview_widget import AssignmentPreviewWidget


class AssignmentPreviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(Path(self.temp.name) / "stage12.db")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )
        self.today = date(2026, 8, 2)
        self.service = AssignmentPreviewService(
            self.session_factory,
            evaluation_date=self.today,
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def _person(
        self,
        number: str,
        *,
        latitude: float | None = 36.801,
        longitude: float | None = 34.650,
        shift: VardiyaTuru = VardiyaTuru.SABAH,
        collar: YakaTuru = YakaTuru.MAVI,
        personnel_type: PersonelTuru = PersonelTuru.KADROLU,
        internship_valid: bool = True,
    ) -> int:
        with self.session_factory.begin() as session:
            person = Personel(
                sicil_no=number,
                ad_soyad=f"Personel {number}",
                acik_adres="Test adresi",
                il="MERSİN",
                ilce="MEZİTLİ",
                mahalle="TEST MAHALLESİ",
                enlem=latitude,
                boylam=longitude,
                vardiya_turu=shift,
                yaka_turu=collar,
                personel_turu=personnel_type,
                staj_baslangic_tarihi=(
                    self.today - timedelta(days=10)
                    if personnel_type == PersonelTuru.STAJYER and internship_valid
                    else None
                ),
                staj_bitis_tarihi=(
                    self.today + timedelta(days=10)
                    if personnel_type == PersonelTuru.STAJYER and internship_valid
                    else None
                ),
                aktif_mi=True,
            )
            session.add(person)
            session.flush()
            return person.id

    def _candidate(
        self,
        code: str,
        *,
        capacity: int = 2,
        shift: VardiyaTuru = VardiyaTuru.SABAH,
        collar: YakaTuru = YakaTuru.MAVI,
        route_name: str | None = None,
        coordinates: list[list[float]] | None = None,
        active: bool = True,
    ) -> tuple[int, int, int]:
        with self.session_factory.begin() as session:
            physical = Servis(servis_kodu=code, aktif_mi=active)
            service_shift = ServisVardiyasi(
                vardiya_turu=shift,
                yaka_turu=collar,
                kapasite=capacity,
                aktif_mi=active,
            )
            physical.vardiyalar.append(service_shift)
            session.add(physical)
            session.flush()
            geometry = {
                "type": "LineString",
                "coordinates": coordinates
                or [[34.600, 36.800], [34.700, 36.800]],
            }
            route = Rota(
                rota_adi=route_name or f"{code} Rotası",
                olusturma_yontemi="MANUEL",
                baslangic_adi="Başlangıç",
                baslangic_enlem=36.8,
                baslangic_boylam=34.6,
                bitis_adi="Seçil Kauçuk",
                bitis_enlem=36.94,
                bitis_boylam=34.98,
                toplam_mesafe_metre=1000,
                toplam_sure_saniye=100,
                geojson=json.dumps(geometry),
                aktif_mi=active,
            )
            session.add(route)
            session.flush()
            link = ServisVardiyaRota(
                servis_vardiya_id=service_shift.id,
                rota_id=route.id,
                vardiya_tanimi=shift.value,
                yaka_turu=collar.value,
                kapasite=capacity,
                aktif_mi=active,
            )
            session.add(link)
            session.flush()
            return service_shift.id, route.id, link.id

    def _active_assignment(self, person_id: int, shift_id: int) -> None:
        with self.session_factory.begin() as session:
            session.add(
                Atama(
                    personel_id=person_id,
                    servis_vardiya_id=shift_id,
                    atama_durumu=AtamaDurumu.ATANDI,
                    aktif_mi=True,
                )
            )

    def _assignment_count(self) -> int:
        with self.session_factory() as session:
            return int(session.scalar(select(func.count()).select_from(Atama)) or 0)

    def test_distance_is_measured_to_full_linestring_not_only_vertices(self) -> None:
        nearest = nearest_point_on_linestring(
            36.801,
            34.650,
            ((34.600, 36.800), (34.700, 36.800)),
        )
        self.assertLess(nearest.distance_meters, 120)
        self.assertAlmostEqual(nearest.longitude, 34.650, places=3)
        self.assertEqual(nearest.segment_index, 0)

    def test_shift_collar_and_proximity_choose_matching_route(self) -> None:
        person_id = self._person("P001")
        self._candidate("S_WRONG", collar=YakaTuru.BEYAZ)
        _, route_id, _ = self._candidate("S_OK")
        preview = self.service.preview_selected((person_id,), max_distance_meters=500)
        result = preview.results[0]
        self.assertTrue(result.assignable)
        self.assertEqual(result.service_code, "S_OK")
        self.assertEqual(result.route_id, route_id)
        self.assertLess(result.distance_meters or 9999, 500)
        self.assertIsNotNone(result.pickup_latitude)

    def test_threshold_exclusion_reports_distance(self) -> None:
        person_id = self._person("P001", latitude=36.85, longitude=34.65)
        self._candidate("S_001")
        result = self.service.preview_selected(
            (person_id,), max_distance_meters=200
        ).results[0]
        self.assertFalse(result.assignable)
        self.assertIn("yakınlık sınırı aşıldı", result.reason)
        self.assertGreater(result.distance_meters or 0, 200)

    def test_bulk_preview_never_exceeds_capacity_or_writes_assignments(self) -> None:
        shift_id, _, _ = self._candidate("S_001", capacity=2)
        occupied = self._person("P000")
        self._active_assignment(occupied, shift_id)
        first = self._person("P001")
        second = self._person("P002")
        before = self._assignment_count()
        preview = self.service.preview_selected(
            (second, first), max_distance_meters=500
        )
        self.assertEqual(preview.assignable_count, 1)
        self.assertEqual(preview.unassignable_count, 1)
        self.assertEqual(preview.results[0].personnel_number, "P001")
        self.assertEqual(preview.results[0].occupancy_after, 2)
        self.assertIn("kapasitesi dolu", preview.results[1].reason)
        self.assertEqual(preview.capacity_needs[0].needed_capacity, 1)
        self.assertEqual(self._assignment_count(), before)

    def test_permanent_personnel_is_allocated_before_intern(self) -> None:
        self._candidate("S_001", capacity=1)
        intern = self._person("P001", personnel_type=PersonelTuru.STAJYER)
        permanent = self._person("P999", personnel_type=PersonelTuru.KADROLU)
        preview = self.service.preview_selected(
            (intern, permanent), max_distance_meters=500
        )
        self.assertEqual(preview.results[0].personnel_id, permanent)
        self.assertTrue(preview.results[0].assignable)
        self.assertFalse(preview.results[1].assignable)

    def test_invalid_internship_and_missing_coordinate_have_explicit_reasons(self) -> None:
        invalid_intern = self._person(
            "P001", personnel_type=PersonelTuru.STAJYER, internship_valid=False
        )
        no_coordinate = self._person("P002", latitude=None, longitude=None)
        self._candidate("S_001")
        preview = self.service.preview_selected(
            (invalid_intern, no_coordinate), max_distance_meters=500
        )
        reasons = {item.personnel_number: item.reason for item in preview.results}
        self.assertIn("Stajyer tarih", reasons["P001"])
        self.assertIn("koordinatı eksik", reasons["P002"])

    def test_inactive_service_route_and_collar_failures_are_explicit(self) -> None:
        no_shift = self._person("P001", shift=VardiyaTuru.AKSAM)
        collar_mismatch = self._person("P002", collar=YakaTuru.BEYAZ)
        no_route = self._person("P003")
        self._candidate("S_INACTIVE", active=False)
        shift_id, route_id, link_id = self._candidate("S_001")
        with self.session_factory.begin() as session:
            session.get(ServisVardiyaRota, link_id).aktif_mi = False
        preview = self.service.preview_selected(
            (no_shift, collar_mismatch, no_route), max_distance_meters=500
        )
        reasons = {item.personnel_number: item.reason for item in preview.results}
        self.assertIn("Vardiyaya uygun", reasons["P001"])
        self.assertIn("Yaka türü", reasons["P002"])
        self.assertIn("Aktif ve geçerli kayıtlı rota", reasons["P003"])

    def test_all_method_excludes_personnel_with_active_assignment(self) -> None:
        shift_id, _, _ = self._candidate("S_001", capacity=5)
        assigned = self._person("P001")
        unassigned = self._person("P002")
        self._active_assignment(assigned, shift_id)
        preview = self.service.preview_all_unassigned(max_distance_meters=500)
        self.assertEqual(preview.personnel_ids, (unassigned,))
        self.assertEqual(preview.results[0].personnel_number, "P002")

    def test_alternative_route_can_be_selected_deterministically(self) -> None:
        person_id = self._person("P001")
        _, _, first_link = self._candidate("S_001", capacity=5)
        _, second_route, second_link = self._candidate(
            "S_002",
            capacity=5,
            coordinates=[[34.600, 36.802], [34.700, 36.802]],
        )
        initial = self.service.preview_selected((person_id,), max_distance_meters=500)
        self.assertEqual(initial.results[0].relation_id, first_link)
        self.assertIn(second_link, {item.relation_id for item in initial.results[0].alternatives})
        changed = self.service.preview_selected(
            (person_id,),
            max_distance_meters=500,
            manual_choices={person_id: second_link},
        )
        self.assertEqual(changed.results[0].route_id, second_route)
        repeated = self.service.preview_selected(
            (person_id,), max_distance_meters=500
        )
        self.assertEqual(repeated.results[0].relation_id, first_link)
        self.assertEqual(self._assignment_count(), 0)

    def test_explicit_commit_creates_assignment_updates_occupancy_and_keeps_route(self) -> None:
        person_id = self._person("P001")
        shift_id, route_id, _ = self._candidate("S_001", capacity=3)
        preview = self.service.preview_selected((person_id,), max_distance_meters=500)
        self.assertEqual(self._assignment_count(), 0)
        committed = self.service.commit_preview(
            preview,
            personnel_ids=(person_id,),
        )
        self.assertEqual(committed.assigned_count, 1)
        with self.session_factory() as session:
            assignment = session.scalar(select(Atama))
            self.assertEqual(assignment.personel_id, person_id)
            self.assertEqual(assignment.servis_vardiya_id, shift_id)
            self.assertEqual(assignment.rota_id, route_id)
            self.assertTrue(assignment.aktif_mi)
            self.assertIsNotNone(assignment.guzergaha_uzaklik_metre)
            self.assertIsNotNone(assignment.guzergahta_en_yakin_nokta_enlem)
        management = ServiceManagementService(self.session_factory)
        shift = management.get_service_shift(shift_id)
        assigned = management.list_assigned_personnel(shift_id)
        self.assertEqual(shift.active_assignment_count, 1)
        self.assertEqual(assigned[0].personnel_number, "P001")
        self.assertEqual(assigned[0].route_name, "S_001 Rotası")

    def test_commit_only_assigns_selected_preview_result(self) -> None:
        first = self._person("P001")
        second = self._person("P002")
        self._candidate("S_001", capacity=2)
        preview = self.service.preview_selected(
            (first, second), max_distance_meters=500
        )
        result = self.service.commit_preview(preview, personnel_ids=(second,))
        self.assertEqual(result.assignments[0].personnel_id, second)
        with self.session_factory() as session:
            assigned_ids = set(session.scalars(select(Atama.personel_id)))
        self.assertEqual(assigned_ids, {second})

    def test_commit_revalidates_capacity_and_rolls_back_when_it_became_full(self) -> None:
        person_id = self._person("P001")
        shift_id, _, _ = self._candidate("S_001", capacity=1)
        preview = self.service.preview_selected((person_id,), max_distance_meters=500)
        other = self._person("P999")
        self._active_assignment(other, shift_id)
        with self.assertRaisesRegex(Exception, "kapasitesi doldu"):
            self.service.commit_preview(preview, personnel_ids=(person_id,))
        self.assertEqual(self._assignment_count(), 1)

    def test_map_contains_route_person_pickup_and_connector(self) -> None:
        person_id = self._person("P001")
        self._candidate("S_001")
        result = self.service.preview_selected(
            (person_id,), max_distance_meters=500
        ).results[0]
        html = render_assignment_preview_html(result)
        self.assertIn("L.geoJSON", html)
        self.assertIn("Önerilen biniş", html)
        self.assertIn("L.polyline([[data.people[0].lat", html)
        self.assertIn("P001", html)

    def test_selected_personnel_is_visible_on_map_before_evaluation(self) -> None:
        self._person("P001")
        option = self.service.list_personnel_options()[0]
        html = render_assignment_preview_html(None, personnel=(option,))
        self.assertIn("P001", html)
        self.assertIn("Test adresi", html)
        self.assertIn("data.people.forEach", html)

    def test_ui_exposes_single_selection_address_map_and_explicit_commit(self) -> None:
        self._person("P001")
        application = QApplication.instance() or QApplication([])
        widget = AssignmentPreviewWidget(service=self.service)
        try:
            self.assertEqual(widget.result_table.columnCount(), 15)
            self.assertGreaterEqual(widget.map_view.minimumWidth(), 620)
            self.assertEqual(widget.status_filter.count(), 4)
            self.assertIn("Değerlendir", widget.single_button.text())
            widget.personnel_list.setCurrentRow(0)
            self.assertEqual(len(widget.personnel_list.selectedItems()), 1)
            self.assertIn("Test adresi", widget.personnel_address_label.text())
            self.assertIn("Onayla", widget.commit_selected_button.text())
            self.assertFalse(widget.commit_selected_button.isEnabled())
        finally:
            widget.close()
        self.assertIsNotNone(application)


if __name__ == "__main__":
    unittest.main()
