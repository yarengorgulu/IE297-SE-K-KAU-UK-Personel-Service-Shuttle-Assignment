"""Güzergâh noktaları manuel yönetim ve Excel aktarım testleri."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import Workbook
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
    ServisGuzergahNoktasi,
    ServisVardiyasi,
    VardiyaTuru,
    YakaTuru,
)
from excel_import import analyze_workbook
from normalization.result import RecordType
from services import (
    DuplicateRoutePointError,
    DuplicateRouteSequenceError,
    RouteManagementService,
    RoutePointFormData,
    RoutePointValidationError,
    ServiceFormData,
    ServiceManagementService,
    ServiceRouteImportError,
    ServiceRouteImportService,
    ServiceShiftFormData,
)
from ui.route_management_widget import RouteManagementWidget


ROUTE_HEADERS = [
    "SERVİS KODU",
    "MAHALLE",
    "İLÇE",
    "ENLEM",
    "BOYLAM",
]


def point_form(
    sequence: object = 1,
    city: object = "MERSİN",
    district: object = "YENİŞEHİR",
    neighborhood: object = "ÇİFTLİK MAHALLESİ",
    point_name: object = None,
    address: object = None,
    description: object = None,
    latitude: object = None,
    longitude: object = None,
    active: bool = True,
) -> RoutePointFormData:
    return RoutePointFormData(
        sequence=sequence,
        city=city,
        district=district,
        neighborhood=neighborhood,
        point_name=point_name,
        address=address,
        description=description,
        latitude=latitude,
        longitude=longitude,
        active=active,
    )


def route_row(
    service_code: object = "S_001",
    neighborhood: object = "ÇİFTLİK MAHALLESİ",
    district: object = "YENİŞEHİR",
    latitude: object = None,
    longitude: object = None,
) -> list[object]:
    return [service_code, neighborhood, district, latitude, longitude]


class FailingRouteImportService(ServiceRouteImportService):
    def _after_point_added(self, session, point, added_count) -> None:
        if added_count == 2:
            raise RuntimeError("Test için güzergâh transaction hatası")


class RouteManagementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.engine = create_database_engine(self.directory / "test.db")
        initialize_database(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )
        self.service_management = ServiceManagementService(self.session_factory)
        self.route_service = RouteManagementService(self.session_factory)
        self.import_service = ServiceRouteImportService(self.session_factory)
        self.workbook_counter = 0

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _count(self, model) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def _create_service(
        self,
        code: str = "S_001",
        *,
        shifts: tuple[tuple[str, str], ...] = (("08:00-16:00", "MAVİ"),),
    ):
        service = self.service_management.create_service(
            ServiceFormData(code),
            ServiceShiftFormData(
                shift=shifts[0][0],
                collar=shifts[0][1],
                capacity=29,
                route_name="MEZİTLİ",
            ),
        )
        for shift, collar in shifts[1:]:
            self.service_management.create_service_shift(
                service.id,
                ServiceShiftFormData(
                    shift=shift,
                    collar=collar,
                    capacity=29,
                    route_name="MEZİTLİ",
                ),
            )
        return service, self.service_management.list_service_shifts(service.id)

    def _report(
        self,
        rows: list[list[object]],
        *,
        headers: list[str] | None = None,
    ):
        self.workbook_counter += 1
        path = self.directory / f"route-{self.workbook_counter}.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Güzergah"
        worksheet.append(headers or ROUTE_HEADERS)
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        workbook.close()
        return analyze_workbook(path, "Güzergah", RecordType.SERVIS_GUZERGAH)

    def test_route_point_can_be_added_to_service_shift(self) -> None:
        _, shifts = self._create_service()
        created = self.route_service.create_point(shifts[0].id, point_form())
        self.assertEqual(created.service_shift_id, shifts[0].id)
        self.assertEqual(created.sequence, 1)
        self.assertEqual(self._count(ServisGuzergahNoktasi), 1)

    def test_duplicate_sequence_is_rejected(self) -> None:
        _, shifts = self._create_service()
        self.route_service.create_point(shifts[0].id, point_form())
        with self.assertRaises(DuplicateRouteSequenceError):
            self.route_service.create_point(
                shifts[0].id,
                point_form(neighborhood="FARKLI MAHALLE"),
            )

    def test_same_physical_service_can_have_different_routes_by_shift(self) -> None:
        _, shifts = self._create_service(
            shifts=(
                ("08:00-16:00", "MAVİ"),
                ("16:00-00:00", "MAVİ"),
            )
        )
        first = self.route_service.create_point(shifts[0].id, point_form())
        second = self.route_service.create_point(
            shifts[1].id,
            point_form(neighborhood="TOROSLAR MAHALLESİ"),
        )
        self.assertNotEqual(first.service_shift_id, second.service_shift_id)
        self.assertEqual(len(self.route_service.list_points(shifts[0].id)), 1)
        self.assertEqual(len(self.route_service.list_points(shifts[1].id)), 1)

    def test_one_shift_can_contain_multiple_districts_and_neighborhoods(self) -> None:
        _, shifts = self._create_service()
        self.route_service.create_point(
            shifts[0].id,
            point_form(1, district="YENİŞEHİR", neighborhood="ÇİFTLİK"),
        )
        self.route_service.create_point(
            shifts[0].id,
            point_form(2, district="MEZİTLİ", neighborhood="TECE"),
        )
        points = self.route_service.list_points(shifts[0].id)
        self.assertEqual([point.district for point in points], ["YENİŞEHİR", "MEZİTLİ"])
        self.assertEqual([point.neighborhood for point in points], ["ÇİFTLİK", "TECE"])

    def test_point_can_be_updated_and_activated_or_deactivated(self) -> None:
        _, shifts = self._create_service()
        created = self.route_service.create_point(shifts[0].id, point_form())
        updated = self.route_service.update_point(
            created.id,
            point_form(
                neighborhood="GÜNCEL MAHALLE",
                point_name="FABRİKA DURAĞI",
                description="Ana giriş",
                active=False,
            ),
        )
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.service_shift_id, shifts[0].id)
        self.assertEqual(updated.neighborhood, "GÜNCEL MAHALLE")
        self.assertFalse(updated.active)
        self.assertTrue(self.route_service.set_point_active(created.id, True).active)

    def test_points_can_be_moved_up_and_down_and_are_resequenced(self) -> None:
        _, shifts = self._create_service()
        first = self.route_service.create_point(shifts[0].id, point_form(1))
        second = self.route_service.create_point(
            shifts[0].id,
            point_form(2, neighborhood="İKİNCİ MAHALLE"),
        )
        third = self.route_service.create_point(
            shifts[0].id,
            point_form(3, neighborhood="ÜÇÜNCÜ MAHALLE"),
        )
        moved_up = self.route_service.move_point(third.id, -1)
        self.assertTrue(moved_up.moved)
        points = self.route_service.list_points(shifts[0].id)
        self.assertEqual([item.id for item in points], [first.id, third.id, second.id])
        self.assertEqual([item.sequence for item in points], [1, 2, 3])

        moved_down = self.route_service.move_point(third.id, 1)
        self.assertTrue(moved_down.moved)
        self.assertEqual(
            [item.id for item in self.route_service.list_points(shifts[0].id)],
            [first.id, second.id, third.id],
        )
        self.assertFalse(self.route_service.move_point(first.id, -1).moved)

    def test_coordinates_can_be_blank(self) -> None:
        _, shifts = self._create_service()
        point = self.route_service.create_point(
            shifts[0].id,
            point_form(latitude=None, longitude=None),
        )
        self.assertIsNone(point.latitude)
        self.assertIsNone(point.longitude)

    def test_invalid_point_information_is_rejected(self) -> None:
        _, shifts = self._create_service()
        with self.assertRaises(RoutePointValidationError):
            self.route_service.create_point(
                shifts[0].id,
                point_form(district=None, neighborhood=None, point_name=None),
            )
        with self.assertRaises(RoutePointValidationError):
            self.route_service.create_point(
                shifts[0].id,
                point_form(latitude=36.8, longitude=None),
            )
        self.assertEqual(self._count(ServisGuzergahNoktasi), 0)

    def test_duplicate_route_point_is_rejected(self) -> None:
        _, shifts = self._create_service()
        self.route_service.create_point(shifts[0].id, point_form())
        with self.assertRaises(DuplicateRoutePointError):
            self.route_service.create_point(
                shifts[0].id,
                point_form(sequence=2, city=" mersin ", neighborhood="çiftlik mahallesi"),
            )

    def test_start_and_end_points_are_visible_in_ordered_views(self) -> None:
        _, shifts = self._create_service()
        self.route_service.create_point(shifts[0].id, point_form(1))
        one_point = self.route_service.list_points(shifts[0].id)
        self.assertEqual(one_point[0].position_label, "Başlangıç / Bitiş")
        self.route_service.create_point(
            shifts[0].id,
            point_form(2, neighborhood="SON MAHALLE"),
        )
        two_points = self.route_service.list_points(shifts[0].id)
        self.assertEqual(two_points[0].position_label, "Başlangıç")
        self.assertEqual(two_points[1].position_label, "Bitiş")

    def test_route_name_is_not_changed_from_point_details(self) -> None:
        _, shifts = self._create_service()
        self.route_service.create_point(
            shifts[0].id,
            point_form(neighborhood="BAŞKA BÖLGE"),
        )
        self.assertEqual(
            self.service_management.get_service_shift(shifts[0].id).route_name,
            "MEZİTLİ",
        )

    def test_excel_route_is_imported_for_unambiguous_shift(self) -> None:
        _, shifts = self._create_service()
        result = self.import_service.import_report(self._report([route_row()]))
        self.assertEqual(result.added_count, 1)
        points = self.route_service.list_points(shifts[0].id)
        self.assertEqual(points[0].neighborhood, "ÇİFTLİK MAHALLESİ")

    def test_s001_through_s016_are_matched_including_s013_s016(self) -> None:
        for number in range(1, 17):
            self._create_service(f"S_{number:03d}")
        report = self._report(
            [
                route_row(
                    service_code=f"S_{number:03d}",
                    neighborhood=f"MAHALLE {number}",
                )
                for number in range(1, 17)
            ]
        )
        result = self.import_service.import_report(report)
        self.assertEqual(result.added_count, 16)
        self.assertEqual(result.missing_service_count, 0)
        with self.session_factory() as session:
            imported_codes = set(
                session.scalars(
                    select(Servis.servis_kodu)
                    .join(Servis.vardiyalar)
                    .join(ServisVardiyasi.guzergah_noktalari)
                )
            )
        self.assertTrue({"S_013", "S_014", "S_015", "S_016"}.issubset(imported_codes))

    def test_route_without_shift_is_added_to_all_service_shifts(self) -> None:
        _, shifts = self._create_service(
            shifts=(
                ("08:00-16:00", "MAVİ"),
                ("16:00-00:00", "MAVİ"),
            )
        )
        result = self.import_service.import_report(self._report([route_row()]))
        self.assertEqual(result.added_count, 2)
        self.assertEqual(result.ambiguous_count, 0)
        self.assertEqual(self._count(ServisGuzergahNoktasi), 2)
        for shift in shifts:
            points = self.route_service.list_points(shift.id)
            self.assertEqual(len(points), 1)
            self.assertEqual(points[0].district, "YENİŞEHİR")
            self.assertEqual(points[0].neighborhood, "ÇİFTLİK MAHALLESİ")

    def test_shift_and_collar_columns_resolve_correct_service_shift(self) -> None:
        _, shifts = self._create_service(
            shifts=(
                ("08:00-16:00", "MAVİ"),
                ("16:00-00:00", "MAVİ"),
            )
        )
        headers = [*ROUTE_HEADERS, "VARDİYA TÜRÜ", "YAKA TÜRÜ"]
        row = [*route_row(), "16:00 00:00", "MAVİ YAKA"]
        result = self.import_service.import_report(
            self._report([row], headers=headers)
        )
        self.assertEqual(result.added_count, 1)
        target_shift = next(item for item in shifts if item.shift == "16:00-00:00")
        self.assertEqual(len(self.route_service.list_points(target_shift.id)), 1)

    def test_missing_service_is_not_created_automatically(self) -> None:
        result = self.import_service.import_report(
            self._report([route_row(service_code="S_999")])
        )
        self.assertEqual(result.missing_service_count, 1)
        self.assertEqual(self._count(Servis), 0)
        self.assertEqual(self._count(ServisGuzergahNoktasi), 0)

    def test_invalid_excel_rows_are_not_imported(self) -> None:
        self._create_service()
        result = self.import_service.import_report(
            self._report([route_row(neighborhood=None, district=None)])
        )
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(self._count(ServisGuzergahNoktasi), 0)

    def test_reimport_does_not_create_duplicate_points(self) -> None:
        self._create_service()
        report = self._report([route_row()])
        self.import_service.import_report(report)
        second = self.import_service.import_report(report)
        self.assertEqual(second.added_count, 0)
        self.assertEqual(second.duplicate_count, 1)
        self.assertEqual(self._count(ServisGuzergahNoktasi), 1)

    def test_route_import_transaction_error_rolls_back_all_points(self) -> None:
        self._create_service()
        report = self._report(
            [
                route_row(neighborhood="BİRİNCİ MAHALLE"),
                route_row(neighborhood="İKİNCİ MAHALLE"),
            ]
        )
        failing = FailingRouteImportService(self.session_factory)
        with self.assertRaises(ServiceRouteImportError):
            failing.import_report(report)
        self.assertEqual(self._count(ServisGuzergahNoktasi), 0)

    def test_route_operations_do_not_change_personnel_or_assignments(self) -> None:
        _, shifts = self._create_service()
        with self.session_factory() as session:
            personnel = Personel(
                sicil_no="P000001",
                acik_adres="Test Adresi",
                vardiya_turu=VardiyaTuru.SABAH,
                yaka_turu=YakaTuru.MAVI,
                personel_turu=PersonelTuru.KADROLU,
            )
            session.add(personnel)
            session.flush()
            assignment = Atama(
                personel_id=personnel.id,
                servis_vardiya_id=shifts[0].id,
                atama_durumu=AtamaDurumu.ATANDI,
                aktif_mi=True,
            )
            session.add(assignment)
            session.commit()
            assignment_id = assignment.id
        self.route_service.create_point(shifts[0].id, point_form())
        self.import_service.import_report(
            self._report([route_row(neighborhood="İKİNCİ MAHALLE")])
        )
        self.assertEqual(self._count(Personel), 1)
        self.assertEqual(self._count(Atama), 1)
        with self.session_factory() as session:
            stored = session.get(Atama, assignment_id)
            self.assertEqual(stored.servis_vardiya_id, shifts[0].id)
            self.assertTrue(stored.aktif_mi)

    def test_table_selection_loads_route_point_into_form(self) -> None:
        _, shifts = self._create_service()
        created = self.route_service.create_point(
            shifts[0].id,
            point_form(point_name="FABRİKA DURAĞI"),
        )
        application = QApplication.instance() or QApplication([])
        widget = RouteManagementWidget(
            service_management=self.service_management,
            route_service=self.route_service,
        )
        try:
            widget._select_point(created.id)
            application.processEvents()
            self.assertEqual(widget.sequence_spin.value(), 1)
            self.assertEqual(widget.point_name_edit.text(), "FABRİKA DURAĞI")
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
