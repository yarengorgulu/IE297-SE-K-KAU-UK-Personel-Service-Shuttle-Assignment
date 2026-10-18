"""Servis kapasite önizlemesinin kontrollü veritabanı aktarım testleri."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from database.connection import create_database_engine
from database.init_db import initialize_database
from database.models import Atama, Personel, Servis, ServisVardiyasi
from excel_import import analyze_workbook
from normalization.result import RecordType
from services import (
    ConflictBehavior,
    ServiceCapacityImportError,
    ServiceCapacityImportService,
)


HEADERS = [
    "SERVİS KODU",
    "SERVİS KAPASİTELERİ",
    "SERVİS ROTASI",
    "YAKA TÜRÜ",
    "VARDİYA TÜRÜ",
]


def capacity_row(
    service_code: object = "S_001",
    capacity: object = 29,
    route_name: object = "TARSUS",
    collar: object = "MAVİ",
    shift: object = "08:00 16:00",
) -> list[object]:
    return [service_code, capacity, route_name, collar, shift]


class FailingServiceCapacityImportService(ServiceCapacityImportService):
    def __init__(self, session_factory) -> None:
        super().__init__(session_factory)
        self.applied_count = 0

    def _apply_entry(self, *args, **kwargs):
        result = super()._apply_entry(*args, **kwargs)
        self.applied_count += 1
        if self.applied_count == 2:
            raise RuntimeError("Test için zorunlu transaction hatası")
        return result


class ServiceCapacityImportTestCase(unittest.TestCase):
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
        self.service = ServiceCapacityImportService(self.session_factory)
        self.workbook_counter = 0

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _report(self, rows: list[list[object]]):
        self.workbook_counter += 1
        path = self.directory / f"capacity-{self.workbook_counter}.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Kapasite"
        worksheet.append(HEADERS)
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        workbook.close()
        return analyze_workbook(path, "Kapasite", RecordType.SERVIS_KAPASITE)

    def _count(self, model) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def test_new_service_code_creates_physical_service(self) -> None:
        result = self.service.import_report(self._report([capacity_row()]))
        self.assertEqual(result.added_service_count, 1)
        self.assertEqual(result.added_shift_count, 1)
        self.assertEqual(self._count(Servis), 1)

    def test_same_service_code_does_not_create_second_physical_service(self) -> None:
        self.service.import_report(self._report([capacity_row()]))
        self.service.import_report(
            self._report([capacity_row(shift="16:00 00:00")])
        )
        self.assertEqual(self._count(Servis), 1)
        self.assertEqual(self._count(ServisVardiyasi), 2)

    def test_same_service_can_be_saved_in_different_shifts(self) -> None:
        report = self._report(
            [
                capacity_row(shift="08:00 16:00"),
                capacity_row(shift="16:00 00:00"),
            ]
        )
        result = self.service.import_report(report)
        self.assertEqual(result.added_service_count, 1)
        self.assertEqual(result.added_shift_count, 2)

    def test_same_service_shift_and_collar_is_not_created_twice(self) -> None:
        report = self._report([capacity_row(), capacity_row(capacity=35)])
        result = self.service.import_report(report)
        self.assertEqual(self._count(ServisVardiyasi), 1)
        self.assertEqual(result.skipped_count, 1)

    def test_conflict_defaults_to_skip(self) -> None:
        self.service.import_report(self._report([capacity_row(capacity=29)]))
        result = self.service.import_report(self._report([capacity_row(capacity=44)]))
        with self.session_factory() as session:
            shift = session.scalar(select(ServisVardiyasi))
            self.assertEqual(shift.kapasite, 29)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 1)

    def test_update_changes_capacity_route_and_active_state(self) -> None:
        self.service.import_report(self._report([capacity_row(capacity=29, route_name="ESKİ")]))
        with self.session_factory() as session:
            shift = session.scalar(select(ServisVardiyasi))
            shift.aktif_mi = False
            session.commit()

        result = self.service.import_report(
            self._report([capacity_row(capacity=46, route_name="YENİ ROTA")]),
            ConflictBehavior.UPDATE,
        )
        with self.session_factory() as session:
            shift = session.scalar(select(ServisVardiyasi))
            self.assertEqual(shift.kapasite, 46)
            self.assertEqual(shift.rota_adi, "YENİ ROTA")
            self.assertTrue(shift.aktif_mi)
        self.assertEqual(result.updated_count, 1)

    def test_invalid_capacity_is_not_saved(self) -> None:
        result = self.service.import_report(
            self._report([capacity_row(capacity=0)])
        )
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(self._count(Servis), 0)
        self.assertEqual(self._count(ServisVardiyasi), 0)

    def test_invalid_rows_are_not_imported(self) -> None:
        report = self._report(
            [
                capacity_row(service_code="S_001"),
                capacity_row(service_code="S_002", shift="09:00 18:00"),
            ]
        )
        result = self.service.import_report(report)
        self.assertEqual(result.added_service_count, 1)
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(self._count(Servis), 1)

    def test_warning_row_requires_confirmation(self) -> None:
        report = self._report([capacity_row()])
        report.rows[0].validation.add_warning(
            "TEST_WARNING",
            "Kullanıcı tarafından kontrol edilmesi gereken uyarı.",
        )
        skipped = self.service.import_report(report, include_warning_rows=False)
        self.assertEqual(skipped.skipped_count, 1)
        self.assertEqual(self._count(Servis), 0)

        imported = self.service.import_report(report, include_warning_rows=True)
        self.assertEqual(imported.imported_warning_count, 1)
        self.assertEqual(self._count(Servis), 1)

    def test_transaction_error_rolls_back_all_previous_rows(self) -> None:
        failing_service = FailingServiceCapacityImportService(self.session_factory)
        report = self._report(
            [
                capacity_row(service_code="S_001"),
                capacity_row(service_code="S_002"),
            ]
        )
        with self.assertRaises(ServiceCapacityImportError):
            failing_service.import_report(report)
        self.assertEqual(self._count(Servis), 0)
        self.assertEqual(self._count(ServisVardiyasi), 0)

    def test_result_counts_added_updated_skipped_and_invalid(self) -> None:
        self.service.import_report(self._report([capacity_row(service_code="S_001")]))
        report = self._report(
            [
                capacity_row(service_code="S_001", capacity=40),
                capacity_row(service_code="S_002"),
                capacity_row(service_code="S_003", capacity=-1),
            ]
        )
        result = self.service.import_report(report)
        self.assertEqual(result.added_service_count, 1)
        self.assertEqual(result.added_shift_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.invalid_count, 1)

    def test_reimport_does_not_create_duplicates(self) -> None:
        report = self._report(
            [
                capacity_row(service_code="S_001"),
                capacity_row(service_code="S_002"),
            ]
        )
        self.service.import_report(report)
        second_result = self.service.import_report(report)
        self.assertEqual(self._count(Servis), 2)
        self.assertEqual(self._count(ServisVardiyasi), 2)
        self.assertEqual(second_result.added_service_count, 0)
        self.assertEqual(second_result.added_shift_count, 0)
        self.assertEqual(second_result.skipped_count, 2)

    def test_import_does_not_write_personnel_table(self) -> None:
        self.service.import_report(self._report([capacity_row()]))
        self.assertEqual(self._count(Personel), 0)

    def test_import_does_not_write_assignment_table(self) -> None:
        self.service.import_report(self._report([capacity_row()]))
        self.assertEqual(self._count(Atama), 0)

    def test_missing_service_codes_are_not_generated(self) -> None:
        self.service.import_report(
            self._report(
                [
                    capacity_row(service_code="S_001"),
                    capacity_row(service_code="S_012"),
                ]
            )
        )
        with self.session_factory() as session:
            codes = set(session.scalars(select(Servis.servis_kodu)))
        self.assertEqual(codes, {"S_001", "S_012"})


if __name__ == "__main__":
    unittest.main()
