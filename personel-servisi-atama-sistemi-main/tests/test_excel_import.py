"""Excel okuma, eşleştirme, analiz ve arka plan çalışma testleri."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import Workbook
from PySide6.QtCore import QEventLoop, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from excel_import import (
    ExcelReadError,
    PreviewStatus,
    analyze_workbook,
    build_column_mapping,
    detect_record_type,
    inspect_workbook,
)
from normalization.result import RecordType
from ui.workers import FunctionWorker
from ui.excel_import_widget import ExcelImportWidget


PERSONNEL_HEADERS = [
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

SERVICE_CAPACITY_HEADERS = [
    "SERVİS KODU",
    "SERVİS KAPASİTELERİ",
    "SERVİS ROTASI",
    "YAKA TÜRÜ",
    "VARDİYA TÜRÜ",
]

SERVICE_ROUTE_HEADERS = [
    "SERVİS KODU",
    "ARAÇ KAPASİTESİ",
    "MAHALLE",
    "İLÇE",
    "ENLEM",
    "BOYLAM",
]


def personnel_values(
    personnel_number: object = "1001",
    latitude: object = 36.8,
    longitude: object = 34.6,
    first_name: object = "Ayşe",
    last_name: object = "Yılmaz",
    active: object = True,
) -> list[object]:
    return [
        personnel_number,
        first_name,
        last_name,
        "MAVİ YAKA",
        "  Atatürk   Cad. No: 1  ",
        "MERSİN",
        "TARSUS",
        "YENİ MAHALLESİ",
        latitude,
        longitude,
        "8:00 16:00",
        "KADROLU ",
        None,
        None,
        active,
    ]


class ExcelImportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_workbook(
        self,
        filename: str,
        sheets: dict[str, list[list[object]]],
    ) -> Path:
        workbook = Workbook()
        first = True
        for sheet_name, rows in sheets.items():
            worksheet = workbook.active if first else workbook.create_sheet()
            first = False
            worksheet.title = sheet_name
            for row in rows:
                worksheet.append(row)
        path = self.directory / filename
        workbook.save(path)
        workbook.close()
        return path

    def test_personnel_file_is_detected(self) -> None:
        detection = detect_record_type(PERSONNEL_HEADERS)
        self.assertTrue(detection.is_confident)
        self.assertEqual(detection.recommended_type, RecordType.PERSONEL)

    def test_personnel_file_without_name_columns_is_detected(self) -> None:
        headers = [
            header for header in PERSONNEL_HEADERS if header not in {"AD", "SOYAD"}
        ]
        detection = detect_record_type(headers)
        self.assertTrue(detection.is_confident)
        self.assertEqual(detection.recommended_type, RecordType.PERSONEL)

    def test_service_capacity_file_is_detected(self) -> None:
        detection = detect_record_type(SERVICE_CAPACITY_HEADERS)
        self.assertTrue(detection.is_confident)
        self.assertEqual(detection.recommended_type, RecordType.SERVIS_KAPASITE)

    def test_service_route_file_is_detected(self) -> None:
        detection = detect_record_type(SERVICE_ROUTE_HEADERS)
        self.assertTrue(detection.is_confident)
        self.assertEqual(detection.recommended_type, RecordType.SERVIS_GUZERGAH)

    def test_selected_file_type_is_read_back_from_qt_combo(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = ExcelImportWidget()
        index = widget.file_type_combo.findData(RecordType.PERSONEL.value)
        widget.file_type_combo.setCurrentIndex(index)
        self.assertEqual(widget._selected_record_type(), RecordType.PERSONEL)
        widget.close()
        self.assertIsNotNone(application)

    def test_empty_worksheet_is_detected(self) -> None:
        path = self._write_workbook(
            "sheets.xlsx",
            {
                "Boş Sayfa": [],
                "Personel": [PERSONNEL_HEADERS, personnel_values()],
            },
        )
        info = inspect_workbook(path)
        self.assertTrue(info.sheets[0].is_empty)
        self.assertFalse(info.sheets[1].is_empty)
        self.assertEqual([sheet.name for sheet in info.non_empty_sheets], ["Personel"])

    def test_dimensionless_worksheet_is_read_in_read_only_mode(self) -> None:
        source = self._write_workbook(
            "dimension-source.xlsx",
            {
                "Güzergah": [
                    SERVICE_ROUTE_HEADERS,
                    ["S_001", 29, "ÇİFTLİK", "YENİŞEHİR", None, None],
                ],
                "Boş": [],
            },
        )
        target = self.directory / "dimensionless.xlsx"
        with ZipFile(source, "r") as input_archive, ZipFile(
            target,
            "w",
            compression=ZIP_DEFLATED,
        ) as output_archive:
            for entry in input_archive.infolist():
                payload = input_archive.read(entry.filename)
                if entry.filename.startswith("xl/worksheets/sheet"):
                    payload = re.sub(rb"<dimension[^>]*/>", b"", payload, count=1)
                output_archive.writestr(entry, payload)

        info = inspect_workbook(target)
        report = analyze_workbook(
            target,
            "Güzergah",
            RecordType.SERVIS_GUZERGAH,
        )
        self.assertEqual(info.non_empty_sheets[0].headers, tuple(SERVICE_ROUTE_HEADERS))
        self.assertTrue(info.sheets[1].is_empty)
        self.assertEqual(report.total_count, 1)
        self.assertTrue(report.rows[0].validation.is_valid)

    def test_missing_required_columns_are_reported(self) -> None:
        mapping = build_column_mapping(
            ["SERVİS KODU", "SERVİS KAPASİTELERİ"],
            RecordType.SERVIS_KAPASITE,
        )
        self.assertFalse(mapping.is_valid)
        self.assertEqual(
            set(mapping.missing_required_columns),
            {"YAKA", "VARDIYA"},
        )

    def test_extra_columns_are_preserved(self) -> None:
        headers = [*SERVICE_CAPACITY_HEADERS, "ARAÇ PLAKASI"]
        path = self._write_workbook(
            "capacity-extra.xlsx",
            {"Kapasite": [headers, ["S_001", 29, "TARSUS", "MAVİ", "08:00 16:00", "33 ABC 123"]]},
        )
        report = analyze_workbook(path, "Kapasite", RecordType.SERVIS_KAPASITE)
        self.assertEqual(report.rows[0].extra_data["ARAÇ PLAKASI"], "33 ABC 123")
        self.assertIn("ARAÇ PLAKASI", report.rows[0].preview_data)

    def test_completely_blank_rows_are_skipped_with_real_row_numbers(self) -> None:
        path = self._write_workbook(
            "blank-row.xlsx",
            {
                "Personel": [
                    PERSONNEL_HEADERS,
                    personnel_values("1001"),
                    [None] * len(PERSONNEL_HEADERS),
                    personnel_values("1002"),
                ]
            },
        )
        report = analyze_workbook(path, "Personel", RecordType.PERSONEL)
        self.assertEqual(report.total_count, 2)
        self.assertEqual(report.skipped_blank_rows, 1)
        self.assertEqual([row.row_number for row in report.rows], [2, 4])

    def test_valid_warning_and_error_counts_are_correct(self) -> None:
        path = self._write_workbook(
            "counts.xlsx",
            {
                "Personel": [
                    PERSONNEL_HEADERS,
                    personnel_values("1001", 36.8, 34.6),
                    personnel_values("1002", None, None),
                    personnel_values(None, 36.8, 34.6),
                ]
            },
        )
        report = analyze_workbook(path, "Personel", RecordType.PERSONEL)
        self.assertEqual(report.total_count, 3)
        self.assertEqual(report.valid_count, 1)
        self.assertEqual(report.warning_count, 1)
        self.assertEqual(report.error_count, 1)

    def test_invalid_row_keeps_error_messages(self) -> None:
        path = self._write_workbook(
            "errors.xlsx",
            {"Personel": [PERSONNEL_HEADERS, personnel_values(None)]},
        )
        report = analyze_workbook(path, "Personel", RecordType.PERSONEL)
        self.assertEqual(report.rows[0].status, PreviewStatus.HATALI)
        self.assertIn("Sicil numarası boş olamaz", report.rows[0].error_text)

    def test_warning_row_keeps_warning_messages(self) -> None:
        path = self._write_workbook(
            "warnings.xlsx",
            {"Personel": [PERSONNEL_HEADERS, personnel_values("1001", None, None)]},
        )
        report = analyze_workbook(path, "Personel", RecordType.PERSONEL)
        self.assertEqual(report.rows[0].status, PreviewStatus.UYARILI)
        self.assertIn("koordinat", report.rows[0].warning_text.lower())

    def test_normalized_data_is_available_for_preview(self) -> None:
        path = self._write_workbook(
            "normalized.xlsx",
            {"Personel": [PERSONNEL_HEADERS, personnel_values()]},
        )
        report = analyze_workbook(path, "Personel", RecordType.PERSONEL)
        preview = report.rows[0].preview_data
        self.assertEqual(preview["YAKA"], "MAVİ")
        self.assertEqual(preview["VARDIYA"], "08:00-16:00")
        self.assertEqual(preview["ACIK_ADRES"], "Atatürk Cad. No: 1")

    def test_analysis_does_not_change_sqlite_database(self) -> None:
        database_path = self.directory / "existing.db"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES ('korunacak')")
            connection.commit()
        finally:
            connection.close()
        before = database_path.read_bytes()
        path = self._write_workbook(
            "no-db-write.xlsx",
            {"Personel": [PERSONNEL_HEADERS, personnel_values()]},
        )
        analyze_workbook(path, "Personel", RecordType.PERSONEL)
        self.assertEqual(database_path.read_bytes(), before)

    def test_large_analysis_runs_without_blocking_qt_event_loop(self) -> None:
        rows = [PERSONNEL_HEADERS]
        rows.extend(personnel_values(str(1000 + index)) for index in range(5000))
        path = self._write_workbook("large.xlsx", {"Personel": rows})

        application = QApplication.instance() or QApplication([])
        event_loop = QEventLoop()
        ui_tick = threading.Event()
        result_holder: list[object] = []
        worker = FunctionWorker(
            lambda: analyze_workbook(path, "Personel", RecordType.PERSONEL)
        )
        worker.signals.result.connect(result_holder.append)
        worker.signals.finished.connect(event_loop.quit)
        QTimer.singleShot(5, ui_tick.set)
        QTimer.singleShot(15000, event_loop.quit)
        QThreadPool.globalInstance().start(worker)
        event_loop.exec()
        QThreadPool.globalInstance().waitForDone(15000)

        self.assertTrue(ui_tick.is_set())
        self.assertEqual(len(result_holder), 1)
        self.assertEqual(result_holder[0].total_count, 5000)
        self.assertIsNotNone(application)

    def test_invalid_excel_file_raises_friendly_error(self) -> None:
        path = self.directory / "invalid.xlsx"
        path.write_bytes(b"this is not an Excel workbook")
        with self.assertRaisesRegex(ExcelReadError, "Excel dosyası okunamadı"):
            inspect_workbook(path)

    def test_manual_mapping_override_is_applied(self) -> None:
        mapping = build_column_mapping(
            ["PERSONEL NUMARASI", *PERSONNEL_HEADERS[1:]],
            RecordType.PERSONEL,
            {0: "SICIL_NO"},
        )
        self.assertTrue(mapping.is_valid)
        self.assertEqual(mapping.entries[0].target_field, "SICIL_NO")
        self.assertTrue(mapping.entries[0].is_manual)

    def test_personnel_import_button_is_enabled_for_valid_personnel_report(self) -> None:
        path = self._write_workbook(
            "personnel-button.xlsx",
            {"Personel": [PERSONNEL_HEADERS, personnel_values()]},
        )
        report = analyze_workbook(path, "Personel", RecordType.PERSONEL)
        application = QApplication.instance() or QApplication([])
        widget = ExcelImportWidget()
        try:
            widget._analysis_ready(report)
            self.assertTrue(widget.import_personnel_button.isEnabled())
            self.assertFalse(widget.import_capacity_button.isEnabled())
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_route_import_button_is_enabled_only_for_valid_route_report(self) -> None:
        path = self._write_workbook(
            "route-button.xlsx",
            {
                "Güzergah": [
                    SERVICE_ROUTE_HEADERS,
                    ["S_001", 29, "ÇİFTLİK", "YENİŞEHİR", None, None],
                ]
            },
        )
        report = analyze_workbook(path, "Güzergah", RecordType.SERVIS_GUZERGAH)
        application = QApplication.instance() or QApplication([])
        widget = ExcelImportWidget()
        try:
            widget._analysis_ready(report)
            self.assertTrue(widget.import_route_button.isEnabled())
            self.assertFalse(widget.import_capacity_button.isEnabled())
            self.assertFalse(widget.import_personnel_button.isEnabled())
        finally:
            widget.close()
        self.assertIsNotNone(application)

    def test_second_personnel_import_click_is_ignored(self) -> None:
        path = self._write_workbook(
            "personnel-double-click.xlsx",
            {"Personel": [PERSONNEL_HEADERS, personnel_values()]},
        )
        report = analyze_workbook(path, "Personel", RecordType.PERSONEL)
        application = QApplication.instance() or QApplication([])
        widget = ExcelImportWidget()
        calls: list[object] = []
        try:
            widget._analysis_ready(report)
            widget._run_async = lambda *args, **kwargs: calls.append((args, kwargs))
            widget._prepare_personnel_import()
            widget._prepare_personnel_import()
            self.assertEqual(len(calls), 1)
        finally:
            widget.close()
        self.assertIsNotNone(application)


if __name__ == "__main__":
    unittest.main()
