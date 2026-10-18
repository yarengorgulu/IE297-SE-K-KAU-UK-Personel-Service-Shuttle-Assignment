"""Excel normalizasyonu ve satır doğrulaması birim testleri."""

from __future__ import annotations

import unittest
from datetime import date

from normalization import (
    RecordType,
    ValueNormalizationError,
    extract_address_roads,
    map_column_name,
    map_columns,
    normalize_collar,
    normalize_personnel_number,
    normalize_shift,
    normalize_status,
    validate_personnel_row,
    validate_service_capacity_row,
    validate_service_route_row,
)
from normalization.text import normalize_header


def valid_personnel_row() -> dict[str, object]:
    return {
        "YENİ SİCİL NO": "P000001",
        "AD": "Ayşe",
        "SOYAD": "Yılmaz",
        "MAVİ YAKA\nBEYAZ YAKA": "MAVİ YAKA",
        "ADRES (OTURDUĞU)": "  Test   Mah.  No: 1  ",
        "İL ": "MERSİN ",
        "İLÇE ": "TARSUS ",
        "MAHALLE ": "TEST MAHALLESİ ",
        "ENLEM ": 36.9,
        "BOYLAM": 34.9,
        "VARDİYA": "8:00 16:00",
        "STATÜ": "KADROLU ",
        "STAJ BAŞLAMA TARİHİ": None,
        "STAJ BİTİŞ TARİHİ": None,
    }


class ColumnNormalizationTests(unittest.TestCase):
    def test_header_line_break_is_replaced_with_space(self) -> None:
        self.assertEqual(
            normalize_header("MAVİ YAKA\nBEYAZ YAKA"),
            "MAVİ YAKA BEYAZ YAKA",
        )

    def test_ascii_yeni_sicil_no_maps_to_personnel_number(self) -> None:
        self.assertEqual(
            map_column_name("YENI SİCİL NO", RecordType.PERSONEL),
            "SICIL_NO",
        )

    def test_personnel_and_route_street_avenue_columns_are_mapped(self) -> None:
        for record_type in (RecordType.PERSONEL, RecordType.SERVIS_GUZERGAH):
            self.assertEqual(map_column_name("SOKAK ADI", record_type), "SOKAK")
            self.assertEqual(map_column_name("CADDE ADI", record_type), "CADDE")

    def test_open_address_separates_street_and_avenue(self) -> None:
        roads = extract_address_roads(
            "YENİ MAH. 33192 SK. ATATÜRK CAD. NO:9 MEZİTLİ MERSİN"
        )
        self.assertEqual(roads.street, "33192 Sokak")
        self.assertEqual(roads.avenue, "ATATÜRK Caddesi")
        compact = extract_address_roads("YENİ MAH. 3907.SK NO:20")
        self.assertEqual(compact.street, "3907. Sokak")

    def test_all_service_capacity_columns_are_mapped(self) -> None:
        result = map_columns(
            [
                "SERVİS KODU",
                "SERVİS KAPASİTELERİ",
                "SERVİS ROTASI",
                "YAKA TÜRÜ",
                "VARDİYA TÜRÜ",
            ],
            RecordType.SERVIS_KAPASITE,
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.unmapped_columns, [])


class ValueNormalizationTests(unittest.TestCase):
    def test_supported_shift_formats_are_normalized(self) -> None:
        cases = {
            "8:00 16:00": "08:00-16:00",
            "08:00 16:00": "08:00-16:00",
            "16:00 00:00": "16:00-00:00",
            "00:00 8:00": "00:00-08:00",
            "00:00 08:00": "00:00-08:00",
            "8:00 17:30": "08:00-17:30",
            "08:00 17:30": "08:00-17:30",
            " 08:00 – 16:00 ": "08:00-16:00",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_shift(source), expected)

    def test_unknown_shift_raises_error(self) -> None:
        with self.assertRaises(ValueNormalizationError) as context:
            normalize_shift("09:00-18:00")
        self.assertEqual(context.exception.code, "INVALID_SHIFT")

    def test_blue_collar_value_is_normalized(self) -> None:
        self.assertEqual(normalize_collar("  mavi yaka "), "MAVİ")

    def test_unknown_collar_raises_error(self) -> None:
        with self.assertRaises(ValueNormalizationError) as context:
            normalize_collar("YEŞİL")
        self.assertEqual(context.exception.code, "INVALID_COLLAR")

    def test_permanent_status_trailing_space_is_removed(self) -> None:
        self.assertEqual(normalize_status("KADROLU "), "KADROLU")

    def test_numeric_personnel_number_does_not_gain_decimal_suffix(self) -> None:
        self.assertEqual(normalize_personnel_number(12345.0), "12345")


class RowValidationTests(unittest.TestCase):
    def test_completely_empty_row_is_invalid(self) -> None:
        result = validate_personnel_row(2, {"YENİ SİCİL NO": " ", "VARDİYA": None})
        self.assertFalse(result.is_valid)
        self.assertIn("EMPTY_ROW", {error.code for error in result.errors})

    def test_missing_personnel_number_is_an_error(self) -> None:
        row = valid_personnel_row()
        row["YENİ SİCİL NO"] = None
        result = validate_personnel_row(2, row)
        self.assertFalse(result.is_valid)
        self.assertTrue(
            any(error.code == "REQUIRED" and error.field == "SICIL_NO" for error in result.errors)
        )

    def test_missing_names_are_warnings_not_errors(self) -> None:
        row = valid_personnel_row()
        row["AD"] = None
        row["SOYAD"] = " "
        result = validate_personnel_row(2, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.normalized_data["AD"], None)
        self.assertEqual(result.normalized_data["SOYAD"], None)
        warning_codes = {warning.code for warning in result.warnings}
        self.assertTrue(
            {"MISSING_FIRST_NAME", "MISSING_LAST_NAME"}.issubset(warning_codes)
        )

    def test_invalid_coordinate_is_an_error(self) -> None:
        row = valid_personnel_row()
        row["ENLEM "] = 91
        result = validate_personnel_row(2, row)
        self.assertFalse(result.is_valid)
        self.assertTrue(
            any(error.code == "INVALID_COORDINATE" and error.field == "ENLEM" for error in result.errors)
        )

    def test_blank_coordinates_create_warning_only(self) -> None:
        row = valid_personnel_row()
        row["ENLEM "] = None
        row["BOYLAM"] = None
        result = validate_personnel_row(2, row)
        self.assertTrue(result.is_valid)
        self.assertIn("MISSING_COORDINATES", {warning.code for warning in result.warnings})

    def test_internship_start_after_end_is_an_error(self) -> None:
        row = valid_personnel_row()
        row["STATÜ"] = "STAJYER"
        row["STAJ BAŞLAMA TARİHİ"] = "2026-09-01"
        row["STAJ BİTİŞ TARİHİ"] = "2026-08-01"
        result = validate_personnel_row(2, row)
        self.assertFalse(result.is_valid)
        self.assertIn("INVALID_DATE_RANGE", {error.code for error in result.errors})

    def test_permanent_personnel_accepts_empty_internship_dates(self) -> None:
        result = validate_personnel_row(2, valid_personnel_row())
        self.assertTrue(result.is_valid)
        self.assertIsNone(result.normalized_data["STAJ_BASLAMA"])
        self.assertIsNone(result.normalized_data["STAJ_BITIS"])

    def test_intern_missing_dates_is_an_error(self) -> None:
        row = valid_personnel_row()
        row["STATÜ"] = "STAJYER"
        result = validate_personnel_row(2, row)
        self.assertFalse(result.is_valid)
        missing_date_errors = [
            error for error in result.errors if error.code == "MISSING_INTERNSHIP_DATE"
        ]
        self.assertEqual(len(missing_date_errors), 2)

    def test_zero_or_negative_capacity_is_an_error(self) -> None:
        for capacity in (0, -1):
            with self.subTest(capacity=capacity):
                result = validate_service_capacity_row(
                    2,
                    {
                        "SERVİS KODU": "S_001",
                        "SERVİS KAPASİTELERİ": capacity,
                        "SERVİS ROTASI": "MEZİTLİ",
                        "YAKA TÜRÜ": "MAVİ",
                        "VARDİYA TÜRÜ": "08:00-16:00",
                    },
                )
                self.assertFalse(result.is_valid)
                self.assertIn("INVALID_CAPACITY", {error.code for error in result.errors})

    def test_original_address_is_preserved(self) -> None:
        row = valid_personnel_row()
        original_address = str(row["ADRES (OTURDUĞU)"])
        result = validate_personnel_row(2, row)
        self.assertEqual(result.normalized_data["ACIK_ADRES_ORIJINAL"], original_address)
        self.assertEqual(result.normalized_data["ACIK_ADRES"], "Test Mah. No: 1")

    def test_suspicious_city_is_warned_without_correction(self) -> None:
        row = valid_personnel_row()
        row["İL "] = "KOACELİ"
        result = validate_personnel_row(2, row)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.normalized_data["IL"], "KOACELİ")
        self.assertIn("SUSPICIOUS_CITY", {warning.code for warning in result.warnings})

    def test_route_row_accepts_blank_coordinates_without_warning(self) -> None:
        result = validate_service_route_row(
            2,
            {
                "SERVIS KODU": "S_016",
                "ARAÇ KAPASİTESİ": 29,
                "MAHALLE": "YENİ MAHALLESİ",
                "İLÇE": "YÜREĞİR",
                "ENLEM": None,
                "BOYLAM": None,
            },
        )
        self.assertTrue(result.is_valid)
        self.assertFalse(result.warnings)

    def test_route_row_rejects_incomplete_coordinates(self) -> None:
        result = validate_service_route_row(
            2,
            {
                "SERVIS KODU": "S_016",
                "MAHALLE": "ÇİFTLİK MAHALLESİ",
                "İLÇE": "YENİŞEHİR",
                "ENLEM": 36.8,
                "BOYLAM": None,
            },
        )
        self.assertFalse(result.is_valid)
        self.assertIn(
            "INCOMPLETE_COORDINATES",
            {error.code for error in result.errors},
        )

    def test_excel_serial_internship_dates_are_converted_to_date(self) -> None:
        row = valid_personnel_row()
        row["STATÜ"] = "STAJYER"
        row["STAJ BAŞLAMA TARİHİ"] = 46216
        row["STAJ BİTİŞ TARİHİ"] = 46248
        result = validate_personnel_row(2, row)
        self.assertTrue(result.is_valid)
        self.assertIsInstance(result.normalized_data["STAJ_BASLAMA"], date)


if __name__ == "__main__":
    unittest.main()
