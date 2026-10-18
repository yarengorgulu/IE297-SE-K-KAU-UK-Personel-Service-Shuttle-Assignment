"""Servis, servis-vardiya ve şoför yönetimi service ve arayüz testleri."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from database.connection import create_database_engine
from database.init_db import initialize_database
from database.models import (
    Atama,
    AtamaDurumu,
    Personel,
    PersonelTuru,
    Servis,
    ServisVardiyasi,
    Sofor,
    VardiyaTuru,
    YakaTuru,
)
from services import (
    CapacityBelowOccupancyError,
    DriverFormData,
    DriverScheduleConflictError,
    DuplicateServiceCodeError,
    DuplicateServiceShiftError,
    ServiceFormData,
    ServiceManagementError,
    ServiceManagementService,
    ServiceManagementValidationError,
    ServiceShiftFormData,
)
from ui.service_management_widget import ServiceManagementWidget


def service_form(
    service_code: str = "S_001",
    driver_id: int | None = None,
    active: bool = True,
) -> ServiceFormData:
    return ServiceFormData(
        service_code=service_code,
        driver_id=driver_id,
        active=active,
    )


def shift_form(
    shift: str = "08:00-16:00",
    collar: str = "MAVİ",
    capacity: object = 16,
    route_name: str | None = "TARSUS",
    active: bool = True,
) -> ServiceShiftFormData:
    return ServiceShiftFormData(
        shift=shift,
        collar=collar,
        capacity=capacity,
        route_name=route_name,
        active=active,
    )


def driver_form(
    first_name: str = "Mehmet",
    last_name: str = "Yılmaz",
    phone: str | None = "0532 000 00 00",
    active: bool = True,
    linked_service_id: int | None = None,
) -> DriverFormData:
    return DriverFormData(
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        active=active,
        linked_service_id=linked_service_id,
    )


class FailingServiceManagementService(ServiceManagementService):
    def _after_service_created(self, session, service) -> None:
        raise RuntimeError("Test için transaction hatası")


class ServiceManagementTestCase(unittest.TestCase):
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
        self.service = ServiceManagementService(self.session_factory)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _count(self, model) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def _create_service_with_shift(
        self,
        code: str = "S_001",
        *,
        driver_id: int | None = None,
        shift: str = "08:00-16:00",
    ):
        service = self.service.create_service(
            service_form(code, driver_id=driver_id),
            shift_form(shift=shift),
        )
        return service, self.service.list_service_shifts(service.id)[0]

    def _add_active_assignment(self, shift_id: int, number: int) -> int:
        with self.session_factory() as session:
            personnel = Personel(
                sicil_no=f"P{number:06d}",
                acik_adres="Test Adresi",
                vardiya_turu=VardiyaTuru.SABAH,
                yaka_turu=YakaTuru.MAVI,
                personel_turu=PersonelTuru.KADROLU,
            )
            session.add(personnel)
            session.flush()
            assignment = Atama(
                personel_id=personnel.id,
                servis_vardiya_id=shift_id,
                atama_durumu=AtamaDurumu.ATANDI,
                aktif_mi=True,
            )
            session.add(assignment)
            session.commit()
            return assignment.id

    def test_new_physical_service_and_first_shift_can_be_added(self) -> None:
        created = self.service.create_service(service_form(), shift_form())
        self.assertEqual(created.service_code, "S_001")
        self.assertEqual(created.shift_count, 1)
        self.assertEqual(self._count(Servis), 1)
        self.assertEqual(self._count(ServisVardiyasi), 1)

    def test_duplicate_service_code_is_not_created(self) -> None:
        self.service.create_service(service_form())
        with self.assertRaises(DuplicateServiceCodeError):
            self.service.create_service(service_form(" s_001 "))
        self.assertEqual(self._count(Servis), 1)

    def test_same_service_can_have_different_shifts(self) -> None:
        service = self.service.create_service(service_form())
        first = self.service.create_service_shift(service.id, shift_form())
        second = self.service.create_service_shift(
            service.id,
            shift_form(shift="16:00-00:00"),
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(self._count(ServisVardiyasi), 2)

    def test_duplicate_service_shift_identity_is_not_created(self) -> None:
        service, _ = self._create_service_with_shift()
        with self.assertRaises(DuplicateServiceShiftError):
            self.service.create_service_shift(
                service.id,
                shift_form(shift="8:00 16:00", collar="MAVİ YAKA"),
            )
        self.assertEqual(self._count(ServisVardiyasi), 1)

    def test_non_positive_or_non_integer_capacity_is_rejected(self) -> None:
        service = self.service.create_service(service_form())
        for invalid_capacity in (0, -1, "1.5", True):
            with self.subTest(capacity=invalid_capacity):
                with self.assertRaises(ServiceManagementValidationError):
                    self.service.create_service_shift(
                        service.id,
                        shift_form(capacity=invalid_capacity),
                    )
        self.assertEqual(self._count(ServisVardiyasi), 0)

    def test_invalid_collar_and_blank_shift_are_rejected(self) -> None:
        service = self.service.create_service(service_form())
        with self.assertRaises(ServiceManagementValidationError):
            self.service.create_service_shift(
                service.id,
                shift_form(collar="YEŞİL"),
            )
        with self.assertRaises(ServiceManagementValidationError):
            self.service.create_service_shift(
                service.id,
                shift_form(shift=""),
            )
        self.assertEqual(self._count(ServisVardiyasi), 0)

    def test_service_shift_fields_can_be_updated(self) -> None:
        _, shift = self._create_service_with_shift()
        updated = self.service.update_service_shift(
            shift.id,
            shift_form(
                shift="16:00 00:00",
                collar="BEYAZ YAKA",
                capacity=29,
                route_name="ADANA",
                active=False,
            ),
        )
        self.assertEqual(updated.shift, "16:00-00:00")
        self.assertEqual(updated.collar, "BEYAZ")
        self.assertEqual(updated.capacity, 29)
        self.assertEqual(updated.route_name, "ADANA")
        self.assertFalse(updated.active)

    def test_updates_preserve_service_and_relationship_ids(self) -> None:
        service, shift = self._create_service_with_shift()
        updated_service = self.service.update_service(
            service.id,
            service_form("S_999", active=False),
        )
        updated_shift = self.service.update_service_shift(
            shift.id,
            shift_form(capacity=20),
        )
        self.assertEqual(updated_service.id, service.id)
        self.assertEqual(updated_service.service_code, "S_001")
        self.assertEqual(updated_shift.id, shift.id)
        self.assertEqual(updated_shift.service_id, service.id)

    def test_service_and_shift_can_be_activated_and_deactivated(self) -> None:
        service, shift = self._create_service_with_shift()
        self.assertFalse(self.service.set_service_active(service.id, False).active)
        self.assertTrue(self.service.set_service_active(service.id, True).active)
        self.assertFalse(
            self.service.set_service_shift_active(shift.id, False).active
        )
        self.assertTrue(self.service.set_service_shift_active(shift.id, True).active)

    def test_capacity_cannot_be_reduced_below_active_occupancy(self) -> None:
        _, shift = self._create_service_with_shift()
        self._add_active_assignment(shift.id, 1)
        self._add_active_assignment(shift.id, 2)
        with self.assertRaises(CapacityBelowOccupancyError):
            self.service.update_service_shift(
                shift.id,
                shift_form(capacity=1),
            )
        self.assertEqual(self.service.get_service_shift(shift.id).capacity, 16)

    def test_driver_can_be_added_and_updated(self) -> None:
        created = self.service.create_driver(driver_form())
        updated = self.service.update_driver(
            created.id,
            driver_form(
                first_name="Ali",
                last_name="Demir",
                phone="0544 111 22 33",
            ),
        )
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.first_name, "Ali")
        self.assertEqual(updated.last_name, "Demir")
        self.assertEqual(updated.phone, "0544 111 22 33")
        self.assertEqual(self._count(Sofor), 1)

    def test_driver_can_be_activated_and_deactivated(self) -> None:
        driver = self.service.create_driver(driver_form())
        self.assertFalse(self.service.set_driver_active(driver.id, False).active)
        self.assertTrue(self.service.set_driver_active(driver.id, True).active)

    def test_driver_can_be_linked_to_physical_service(self) -> None:
        service = self.service.create_service(service_form())
        driver = self.service.create_driver(
            driver_form(linked_service_id=service.id)
        )
        stored_service = self.service.get_service(service.id)
        self.assertEqual(stored_service.driver_id, driver.id)
        self.assertIn(service.id, self.service.get_driver(driver.id).service_ids)

    def test_driver_can_use_non_overlapping_shifts_but_not_overlapping_shift(self) -> None:
        driver = self.service.create_driver(driver_form())
        self._create_service_with_shift(
            "S_001",
            driver_id=driver.id,
            shift="08:00-16:00",
        )
        second, _ = self._create_service_with_shift(
            "S_002",
            driver_id=driver.id,
            shift="16:00-00:00",
        )
        with self.assertRaises(DriverScheduleConflictError):
            self.service.create_service_shift(
                second.id,
                shift_form(shift="08:00-16:00", collar="BEYAZ"),
            )
        self.assertEqual(self._count(ServisVardiyasi), 2)

    def test_transaction_error_rolls_back_service_and_first_shift(self) -> None:
        failing_service = FailingServiceManagementService(self.session_factory)
        with self.assertRaises(ServiceManagementError):
            failing_service.create_service(service_form(), shift_form())
        self.assertEqual(self._count(Servis), 0)
        self.assertEqual(self._count(ServisVardiyasi), 0)

    def test_management_operations_do_not_change_assignment_table(self) -> None:
        service, shift = self._create_service_with_shift()
        assignment_id = self._add_active_assignment(shift.id, 1)
        driver = self.service.create_driver(driver_form())
        self.service.update_service(
            service.id,
            service_form("IGNORED", driver_id=driver.id),
        )
        self.service.update_service_shift(
            shift.id,
            shift_form(capacity=20, route_name="YENİ ROTA"),
        )
        self.service.update_driver(
            driver.id,
            driver_form(first_name="Güncel", last_name="Şoför"),
        )
        with self.session_factory() as session:
            assignment = session.get(Atama, assignment_id)
            self.assertIsNotNone(assignment)
            self.assertEqual(assignment.servis_vardiya_id, shift.id)
            self.assertTrue(assignment.aktif_mi)
        self.assertEqual(self._count(Atama), 1)

    def test_driver_schema_preserves_full_name_and_adds_name_columns(self) -> None:
        columns = {item["name"] for item in inspect(self.engine).get_columns("sofor")}
        self.assertIn("ad", columns)
        self.assertIn("soyad", columns)
        self.assertIn("ad_soyad", columns)
        self.assertIn("telefon", columns)

    def test_table_selection_loads_service_into_form(self) -> None:
        created = self.service.create_service(service_form(), shift_form())
        application = QApplication.instance() or QApplication([])
        widget = ServiceManagementWidget(service=self.service)
        try:
            widget.service_widget._select_service(created.id)
            application.processEvents()
            self.assertEqual(
                widget.service_widget.service_code_edit.text(),
                "S_001",
            )
            self.assertTrue(widget.service_widget.service_code_edit.isReadOnly())
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
