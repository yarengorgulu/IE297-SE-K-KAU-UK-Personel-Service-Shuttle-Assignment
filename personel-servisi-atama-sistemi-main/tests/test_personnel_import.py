"""Doğrulanmış personel önizlemesinin kontrollü veritabanı aktarım testleri."""

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
    PersonnelImportError,
    PersonnelImportService,
)


HEADERS = [
    "YENİ SİCİL NO",
    "AD",
    "SOYAD",
    "MAVİ YAKA BEYAZ YAKA",
    "ADRES (OTURDUĞU)",
    "İL",
    "İLÇE",
    "MAHALLE",
    "ENLEM",
    "BOYLAM",
    "VARDİYA",
    "STATÜ",
    "STAJ BAŞLAMA TARİHİ",
    "STAJ BİTİŞ TARİHİ",
    "AKTİF Mİ",
]


def personnel_row(
    personnel_number: object = "P000001",
    first_name: object = "Ayşe",
    last_name: object = "Yılmaz",
    collar: object = "MAVİ YAKA",
    address: object = "  Atatürk   Cad. No: 1  ",
    city: object = "MERSİN",
    district: object = "TARSUS",
    neighborhood: object = "YENİ MAHALLESİ",
    latitude: object = 36.8,
    longitude: object = 34.6,
    shift: object = "08:00 16:00",
    status: object = "KADROLU",
    internship_start: object = None,
    internship_end: object = None,
    active: object = True,
) -> list[object]:
    return [
        personnel_number,
        first_name,
        last_name,
        collar,
        address,
        city,
        district,
        neighborhood,
        latitude,
        longitude,
        shift,
        status,
        internship_start,
        internship_end,
        active,
    ]


class FailingPersonnelImportService(PersonnelImportService):
    def __init__(self, session_factory) -> None:
        super().__init__(session_factory)
        self.applied_count = 0

    def _apply_entry(self, *args, **kwargs):
        result = super()._apply_entry(*args, **kwargs)
        self.applied_count += 1
        if self.applied_count == 2:
            raise RuntimeError("Test için zorunlu transaction hatası")
        return result


class PersonnelImportTestCase(unittest.TestCase):
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
        self.service = PersonnelImportService(self.session_factory)
        self.workbook_counter = 0

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _report(
        self,
        rows: list[list[object]],
        *,
        include_active: bool = True,
        include_names: bool = True,
    ):
        self.workbook_counter += 1
        path = self.directory / f"personnel-{self.workbook_counter}.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Personel"
        included_indexes = [
            index
            for index in range(len(HEADERS))
            if (include_names or index not in {1, 2})
            and (include_active or index != len(HEADERS) - 1)
        ]
        worksheet.append([HEADERS[index] for index in included_indexes])
        for row in rows:
            worksheet.append([row[index] for index in included_indexes])
        workbook.save(path)
        workbook.close()
        return analyze_workbook(path, "Personel", RecordType.PERSONEL)

    def _count(self, model) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def _personnel(self, personnel_number: str = "P000001") -> Personel | None:
        with self.session_factory() as session:
            return session.scalar(
                select(Personel).where(Personel.sicil_no == personnel_number)
            )

    def test_new_personnel_is_added(self) -> None:
        result = self.service.import_report(self._report([personnel_row()]))
        person = self._personnel()
        self.assertIsNotNone(person)
        self.assertEqual(person.ad, "Ayşe")
        self.assertEqual(person.soyad, "Yılmaz")
        self.assertEqual(person.acik_adres, "Atatürk Cad. No: 1")
        self.assertIsNone(person.sokak)
        self.assertEqual(person.cadde, "Atatürk Caddesi")
        self.assertEqual(result.added_count, 1)

    def test_same_personnel_number_is_not_created_twice(self) -> None:
        report = self._report([personnel_row()])
        self.service.import_report(report)
        second = self.service.import_report(report)
        self.assertEqual(self._count(Personel), 1)
        self.assertEqual(second.added_count, 0)
        self.assertEqual(second.skipped_count, 1)

    def test_conflict_defaults_to_skip(self) -> None:
        self.service.import_report(self._report([personnel_row(first_name="Ayşe")]))
        result = self.service.import_report(
            self._report([personnel_row(first_name="Fatma")])
        )
        self.assertEqual(self._personnel().ad, "Ayşe")
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 1)

    def test_update_preserves_identity_and_changes_profile(self) -> None:
        self.service.import_report(self._report([personnel_row()]))
        original = self._personnel()
        original_id = original.id
        result = self.service.import_report(
            self._report(
                [
                    personnel_row(
                        first_name="Fatma",
                        last_name="Demir",
                        collar="BEYAZ",
                        address="Yeni Adres No: 2",
                        shift="16:00 00:00",
                        active=False,
                    )
                ]
            ),
            ConflictBehavior.UPDATE,
        )
        updated = self._personnel()
        self.assertEqual(updated.id, original_id)
        self.assertEqual(updated.sicil_no, "P000001")
        self.assertEqual(updated.ad_soyad, "Fatma Demir")
        self.assertEqual(updated.acik_adres, "Yeni Adres No: 2")
        self.assertEqual(updated.yaka_turu.value, "BEYAZ")
        self.assertEqual(updated.vardiya_turu.value, "16:00-00:00")
        self.assertFalse(updated.aktif_mi)
        self.assertEqual(result.updated_count, 1)

    def test_blank_personnel_number_or_shift_is_not_imported(self) -> None:
        report = self._report(
            [
                personnel_row(personnel_number=None),
                personnel_row(personnel_number="P4", shift=None),
            ]
        )
        result = self.service.import_report(report)
        self.assertEqual(result.invalid_count, 2)
        self.assertEqual(self._count(Personel), 0)

    def test_blank_names_are_imported_as_warnings_with_approval(self) -> None:
        report = self._report(
            [personnel_row(first_name=None, last_name=None)]
        )
        self.assertEqual(report.warning_count, 1)
        result = self.service.import_report(report, include_warning_rows=True)
        personnel = self._personnel()
        self.assertEqual(result.added_count, 1)
        self.assertEqual(result.imported_warning_count, 1)
        self.assertIsNone(personnel.ad)
        self.assertIsNone(personnel.soyad)
        self.assertIsNone(personnel.ad_soyad)

    def test_missing_name_columns_are_imported_as_warnings_with_approval(self) -> None:
        report = self._report([personnel_row()], include_names=False)
        self.assertTrue(report.mapping.is_valid)
        self.assertEqual(report.warning_count, 1)
        result = self.service.import_report(report, include_warning_rows=True)
        personnel = self._personnel()
        self.assertEqual(result.added_count, 1)
        self.assertIsNone(personnel.ad)
        self.assertIsNone(personnel.soyad)

    def test_missing_name_columns_preserve_existing_names_on_update(self) -> None:
        self.service.import_report(self._report([personnel_row()]))
        report = self._report(
            [personnel_row(address="Güncel Adres No: 3")],
            include_names=False,
        )
        self.service.import_report(
            report,
            ConflictBehavior.UPDATE,
            include_warning_rows=True,
        )
        personnel = self._personnel()
        self.assertEqual(personnel.ad, "Ayşe")
        self.assertEqual(personnel.soyad, "Yılmaz")
        self.assertEqual(personnel.ad_soyad, "Ayşe Yılmaz")
        self.assertEqual(personnel.acik_adres, "Güncel Adres No: 3")

    def test_invalid_collar_is_not_imported(self) -> None:
        result = self.service.import_report(
            self._report([personnel_row(collar="YEŞİL")])
        )
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(self._count(Personel), 0)

    def test_invalid_rows_are_not_imported(self) -> None:
        result = self.service.import_report(
            self._report(
                [
                    personnel_row(personnel_number="P1"),
                    personnel_row(personnel_number="P2", shift="09:00 18:00"),
                ]
            )
        )
        self.assertEqual(result.added_count, 1)
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(self._count(Personel), 1)

    def test_warning_row_requires_confirmation(self) -> None:
        report = self._report([personnel_row(latitude=None, longitude=None)])
        skipped = self.service.import_report(report, include_warning_rows=False)
        self.assertEqual(skipped.skipped_count, 1)
        self.assertEqual(self._count(Personel), 0)

        imported = self.service.import_report(report, include_warning_rows=True)
        self.assertEqual(imported.imported_warning_count, 1)
        self.assertEqual(self._count(Personel), 1)

    def test_reimport_does_not_create_duplicates(self) -> None:
        report = self._report(
            [
                personnel_row(personnel_number="P1"),
                personnel_row(personnel_number="P2"),
            ]
        )
        self.service.import_report(report)
        second = self.service.import_report(report)
        self.assertEqual(self._count(Personel), 2)
        self.assertEqual(second.skipped_count, 2)

    def test_transaction_error_rolls_back_all_previous_rows(self) -> None:
        failing_service = FailingPersonnelImportService(self.session_factory)
        report = self._report(
            [
                personnel_row(personnel_number="P1"),
                personnel_row(personnel_number="P2"),
            ]
        )
        with self.assertRaises(PersonnelImportError):
            failing_service.import_report(report)
        self.assertEqual(self._count(Personel), 0)

    def test_result_counts_are_correct(self) -> None:
        self.service.import_report(self._report([personnel_row(personnel_number="P1")]))
        result = self.service.import_report(
            self._report(
                [
                    personnel_row(personnel_number="P1"),
                    personnel_row(personnel_number="P2"),
                    personnel_row(personnel_number="P3", shift=None),
                ]
            )
        )
        self.assertEqual(result.added_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.invalid_count, 1)

    def test_service_and_assignment_tables_do_not_change(self) -> None:
        self.service.import_report(self._report([personnel_row()]))
        self.assertEqual(self._count(Servis), 0)
        self.assertEqual(self._count(ServisVardiyasi), 0)
        self.assertEqual(self._count(Atama), 0)

    def test_same_name_with_different_numbers_is_allowed(self) -> None:
        self.service.import_report(
            self._report(
                [
                    personnel_row(personnel_number="P1"),
                    personnel_row(personnel_number="P2"),
                ]
            )
        )
        self.assertEqual(self._count(Personel), 2)

    def test_missing_active_column_defaults_to_active(self) -> None:
        self.service.import_report(
            self._report([personnel_row(active=None)], include_active=False)
        )
        self.assertTrue(self._personnel().aktif_mi)

    def test_missing_active_column_preserves_existing_active_state_on_update(self) -> None:
        self.service.import_report(self._report([personnel_row(active=False)]))
        self.service.import_report(
            self._report(
                [personnel_row(first_name="Fatma", active=None)],
                include_active=False,
            ),
            ConflictBehavior.UPDATE,
        )
        personnel = self._personnel()
        self.assertEqual(personnel.ad, "Fatma")
        self.assertFalse(personnel.aktif_mi)


if __name__ == "__main__":
    unittest.main()
