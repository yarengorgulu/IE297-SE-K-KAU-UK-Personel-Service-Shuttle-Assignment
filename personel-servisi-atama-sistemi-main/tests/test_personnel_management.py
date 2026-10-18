"""Manuel personel yönetimi service ve arayüz testleri."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from database.connection import create_database_engine
from database.init_db import initialize_database
from database.models import (
    Atama,
    Personel,
    Servis,
    ServisVardiyasi,
    VardiyaTuru,
    YakaTuru,
)
from services import (
    DuplicatePersonnelNumberError,
    PersonnelFormData,
    PersonnelManagementService,
    PersonnelValidationError,
    PersonnelWarningsRequireConfirmation,
)
from ui.personnel_management_widget import PersonnelManagementWidget


def form_data(
    personnel_number: str = "P000001",
    first_name: str | None = "Ayşe",
    last_name: str | None = "Yılmaz",
    address: str = "  Atatürk   Cad. No: 1  ",
    city: str = "MERSİN",
    district: str = "TARSUS",
    neighborhood: str = "YENİ MAHALLESİ",
    shift: str = "8:00 16:00",
    collar: str = "MAVİ YAKA",
    personnel_type: str = "KADROLU",
    internship_start: object = None,
    internship_end: object = None,
    active: bool = True,
) -> PersonnelFormData:
    return PersonnelFormData(
        personnel_number=personnel_number,
        first_name=first_name,
        last_name=last_name,
        address=address,
        city=city,
        district=district,
        neighborhood=neighborhood,
        shift=shift,
        collar=collar,
        personnel_type=personnel_type,
        internship_start=internship_start,
        internship_end=internship_end,
        active=active,
    )


class PersonnelManagementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.engine = create_database_engine(database_path)
        initialize_database(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )
        self.service = PersonnelManagementService(self.session_factory)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _create(self, data: PersonnelFormData | None = None):
        return self.service.create_personnel(
            data or form_data(),
            allow_warnings=True,
        )

    def _count(self, model) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def test_new_personnel_can_be_added_manually(self) -> None:
        result = self._create()
        self.assertEqual(result.personnel.personnel_number, "P000001")
        self.assertEqual(self._count(Personel), 1)

    def test_manual_personnel_receives_system_managed_import_group(self) -> None:
        created = self._create()
        with self.session_factory() as session:
            personnel = session.get(Personel, created.personnel.id)
            self.assertEqual(personnel.aktarim_grup_kodu, "MANUEL")

    def test_intern_can_be_added_with_required_dates(self) -> None:
        result = self._create(
            form_data(
                personnel_type="STAJYER",
                internship_start="2026-07-01",
                internship_end="2026-08-31",
            )
        )
        self.assertEqual(result.personnel.personnel_type, "STAJYER")
        self.assertEqual(result.personnel.internship_start, date(2026, 7, 1))
        self.assertEqual(result.personnel.internship_end, date(2026, 8, 31))

    def test_intern_without_dates_is_not_saved(self) -> None:
        with self.assertRaises(PersonnelValidationError):
            self.service.create_personnel(
                form_data(personnel_type="STAJYER"),
                allow_warnings=True,
            )
        self.assertEqual(self._count(Personel), 0)

    def test_blank_personnel_number_is_not_saved(self) -> None:
        with self.assertRaises(PersonnelValidationError):
            self.service.create_personnel(
                form_data(personnel_number=" "),
                allow_warnings=True,
            )
        self.assertEqual(self._count(Personel), 0)

    def test_duplicate_personnel_number_is_not_created(self) -> None:
        self._create()
        with self.assertRaises(DuplicatePersonnelNumberError):
            self._create()
        self.assertEqual(self._count(Personel), 1)

    def test_blank_name_warns_and_can_be_saved_with_confirmation(self) -> None:
        data = form_data(first_name=None, last_name=" ")
        with self.assertRaises(PersonnelWarningsRequireConfirmation) as context:
            self.service.create_personnel(data)
        warning_text = " ".join(context.exception.messages)
        self.assertIn("Ad bilgisi boş", warning_text)
        self.assertIn("Soyad bilgisi boş", warning_text)

        result = self.service.create_personnel(data, allow_warnings=True)
        self.assertIsNone(result.personnel.first_name)
        self.assertIsNone(result.personnel.last_name)

    def test_invalid_collar_is_not_saved(self) -> None:
        with self.assertRaises(PersonnelValidationError):
            self.service.create_personnel(
                form_data(collar="YEŞİL"),
                allow_warnings=True,
            )
        self.assertEqual(self._count(Personel), 0)

    def test_missing_shift_is_not_saved(self) -> None:
        with self.assertRaises(PersonnelValidationError):
            self.service.create_personnel(
                form_data(shift=""),
                allow_warnings=True,
            )
        self.assertEqual(self._count(Personel), 0)

    def test_personnel_is_found_by_personnel_number(self) -> None:
        self._create(form_data(personnel_number="P100"))
        self._create(form_data(personnel_number="P200"))
        result = self.service.search_by_personnel_number("P200")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].personnel_number, "P200")

    def test_personnel_information_can_be_updated(self) -> None:
        created = self._create()
        result = self.service.update_personnel(
            created.personnel.id,
            form_data(
                personnel_number="DEĞİŞTİRİLMEMELİ",
                first_name="Fatma",
                last_name="Demir",
                address="Yeni Adres No: 2",
                shift="16:00 00:00",
                collar="BEYAZ YAKA",
                active=False,
            ),
            allow_warnings=True,
        )
        self.assertEqual(result.personnel.first_name, "Fatma")
        self.assertEqual(result.personnel.last_name, "Demir")
        self.assertEqual(result.personnel.shift, "16:00-00:00")
        self.assertEqual(result.personnel.collar, "BEYAZ")
        self.assertFalse(result.personnel.active)

    def test_update_preserves_database_id_and_personnel_number(self) -> None:
        created = self._create()
        result = self.service.update_personnel(
            created.personnel.id,
            form_data(personnel_number="P999999", first_name="Güncel"),
            allow_warnings=True,
        )
        self.assertEqual(result.personnel.id, created.personnel.id)
        self.assertEqual(result.personnel.personnel_number, "P000001")

    def test_personnel_can_be_activated_and_deactivated(self) -> None:
        created = self._create()
        inactive = self.service.set_personnel_active(created.personnel.id, False)
        self.assertFalse(inactive.active)
        active = self.service.set_personnel_active(created.personnel.id, True)
        self.assertTrue(active.active)

    def test_manual_save_uses_shared_normalization_rules(self) -> None:
        result = self._create(
            form_data(
                first_name="  Ayşe  ",
                last_name="  Yılmaz  ",
                address="  Atatürk   Cad.   No: 1  ",
                shift="8:00 16:00",
                collar="MAVİ YAKA",
            )
        )
        self.assertEqual(result.personnel.first_name, "Ayşe")
        self.assertEqual(result.personnel.last_name, "Yılmaz")
        self.assertEqual(result.personnel.address, "Atatürk Cad. No: 1")
        self.assertEqual(result.personnel.shift, "08:00-16:00")
        self.assertEqual(result.personnel.collar, "MAVİ")

    def test_assignment_review_warning_is_created_for_route_affecting_change(self) -> None:
        created = self._create()
        unchanged = self.service.update_personnel(
            created.personnel.id,
            form_data(first_name="Fatma"),
            allow_warnings=True,
        )
        self.assertFalse(unchanged.assignment_review_recommended)

        changed = self.service.update_personnel(
            created.personnel.id,
            form_data(first_name="Fatma", address="Başka Adres No: 4"),
            allow_warnings=True,
        )
        self.assertTrue(changed.assignment_review_recommended)

    def test_manual_operations_do_not_change_assignment_table(self) -> None:
        created = self._create()
        self.service.update_personnel(
            created.personnel.id,
            form_data(address="Yeni Adres"),
            allow_warnings=True,
        )
        self.service.set_personnel_active(created.personnel.id, False)
        self.assertEqual(self._count(Atama), 0)

    def test_manual_operations_do_not_change_service_capacity(self) -> None:
        with self.session_factory() as session:
            service = Servis(servis_kodu="S_001")
            service.vardiyalar.append(
                ServisVardiyasi(
                    vardiya_turu=VardiyaTuru.SABAH,
                    yaka_turu=YakaTuru.MAVI,
                    kapasite=29,
                )
            )
            session.add(service)
            session.commit()

        created = self._create()
        self.service.update_personnel(
            created.personnel.id,
            form_data(address="Yeni Adres"),
            allow_warnings=True,
        )
        self.service.set_personnel_active(created.personnel.id, False)

        with self.session_factory() as session:
            shift = session.scalar(select(ServisVardiyasi))
            self.assertEqual(shift.kapasite, 29)
            self.assertTrue(shift.aktif_mi)
        self.assertEqual(self._count(Servis), 1)
        self.assertEqual(self._count(ServisVardiyasi), 1)

    def test_table_selection_loads_personnel_into_form(self) -> None:
        created = self._create(
            form_data(personnel_number="P321", first_name="Sevgi", last_name="Kaya")
        )
        application = QApplication.instance() or QApplication([])
        widget = PersonnelManagementWidget(service=self.service)
        try:
            widget._select_personnel(created.personnel.id)
            application.processEvents()
            self.assertEqual(widget.personnel_number_edit.text(), "P321")
            self.assertEqual(widget.first_name_edit.text(), "Sevgi")
            self.assertEqual(widget.last_name_edit.text(), "Kaya")
            self.assertTrue(widget.personnel_number_edit.isReadOnly())
            self.assertEqual(widget.personnel_type_combo.currentData(), "KADROLU")
        finally:
            widget.close()

    def test_internship_date_fields_are_visible_only_for_intern(self) -> None:
        application = QApplication.instance() or QApplication([])
        widget = PersonnelManagementWidget(service=self.service)
        try:
            widget._start_new_record()
            application.processEvents()
            self.assertTrue(widget.internship_start_edit.isHidden())
            self.assertTrue(widget.internship_end_edit.isHidden())

            widget._set_combo_value(widget.personnel_type_combo, "STAJYER")
            application.processEvents()
            self.assertFalse(widget.internship_start_edit.isHidden())
            self.assertFalse(widget.internship_end_edit.isHidden())
        finally:
            widget.close()

    def test_exact_search_loads_form_and_clear_search_restores_list(self) -> None:
        self._create(form_data(personnel_number="P100"))
        second = self._create(
            form_data(personnel_number="P200", first_name="Deniz", last_name="Kaya")
        )
        application = QApplication.instance() or QApplication([])
        widget = PersonnelManagementWidget(service=self.service)
        try:
            widget.search_edit.setText(second.personnel.personnel_number)
            widget._search()
            application.processEvents()
            self.assertEqual(widget.personnel_table.rowCount(), 1)
            self.assertEqual(widget.personnel_number_edit.text(), "P200")
            self.assertEqual(widget.first_name_edit.text(), "Deniz")

            widget._clear_search()
            application.processEvents()
            self.assertEqual(widget.personnel_table.rowCount(), 2)
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
