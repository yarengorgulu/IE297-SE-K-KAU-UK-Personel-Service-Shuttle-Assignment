"""Servis-vardiya listeleme, ekleme ve güncelleme ekranı."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.models import VardiyaTuru, YakaTuru
from services import (
    ServiceManagementError,
    ServiceManagementService,
    ServiceShiftFormData,
    ServiceShiftView,
)


logger = logging.getLogger(__name__)


class ServiceShiftManagementWidget(QWidget):
    data_changed = Signal()

    def __init__(
        self,
        parent=None,
        *,
        service: ServiceManagementService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or ServiceManagementService()
        self._selected_shift_id: int | None = None
        self._selected_active: bool | None = None
        self._new_record_mode = False
        self._build_ui()
        self._connect_signals()
        self.refresh_data()
        self._clear_form()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        filter_layout = QHBoxLayout()
        self.filter_service_combo = QComboBox()
        self.refresh_button = QPushButton("Listeyi Yenile")
        filter_layout.addWidget(QLabel("Servise Göre Filtre:"))
        filter_layout.addWidget(self.filter_service_combo, 1)
        filter_layout.addWidget(self.refresh_button)
        root.addLayout(filter_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_group = QGroupBox("Servis-Vardiya Listesi")
        list_layout = QVBoxLayout(list_group)
        self.shift_table = QTableWidget()
        self.shift_table.setColumnCount(7)
        self.shift_table.setHorizontalHeaderLabels(
            ["Servis", "Vardiya", "Yaka", "Kapasite", "Doluluk", "Rota", "Durum"]
        )
        self.shift_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.shift_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.shift_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.shift_table.setAlternatingRowColors(True)
        self.shift_table.horizontalHeader().setStretchLastSection(True)
        list_layout.addWidget(self.shift_table, 1)
        self.count_label = QLabel("Toplam: 0")
        list_layout.addWidget(self.count_label)
        splitter.addWidget(list_group)

        form_group = QGroupBox("Servis-Vardiya Bilgileri")
        form_root = QVBoxLayout(form_group)
        form = QFormLayout()
        self.service_combo = QComboBox()
        self.shift_combo = QComboBox()
        for shift in VardiyaTuru:
            self.shift_combo.addItem(shift.value, shift.value)
        self.collar_combo = QComboBox()
        for collar in YakaTuru:
            self.collar_combo.addItem(collar.value, collar.value)
        self.capacity_spin = QSpinBox()
        self.capacity_spin.setRange(1, 10000)
        self.capacity_spin.setValue(16)
        self.route_name_edit = QLineEdit()
        self.active_check = QCheckBox("Servis vardiyası aktif")
        self.occupancy_label = QLabel("0")
        form.addRow("Fiziksel Servis*:", self.service_combo)
        form.addRow("Vardiya*:", self.shift_combo)
        form.addRow("Yaka*:", self.collar_combo)
        form.addRow("Kapasite*:", self.capacity_spin)
        form.addRow("Aktif Doluluk:", self.occupancy_label)
        form.addRow("Rota Adı:", self.route_name_edit)
        form.addRow("Aktiflik:", self.active_check)
        form_root.addLayout(form)

        primary_buttons = QHBoxLayout()
        secondary_buttons = QHBoxLayout()
        self.new_button = QPushButton("Yeni Kayıt")
        self.save_button = QPushButton("Kaydet")
        self.update_button = QPushButton("Güncelle")
        self.clear_button = QPushButton("Temizle")
        self.toggle_active_button = QPushButton("Aktif/Pasif")
        for button in (self.new_button, self.save_button, self.update_button):
            primary_buttons.addWidget(button)
        for button in (self.clear_button, self.toggle_active_button):
            secondary_buttons.addWidget(button)
        form_root.addLayout(primary_buttons)
        form_root.addLayout(secondary_buttons)
        form_root.addStretch()
        splitter.addWidget(form_group)
        splitter.setSizes([700, 400])
        root.addWidget(splitter, 1)

        assigned_group = QGroupBox("Seçili Servis-Vardiyaya Atanan Personeller")
        assigned_layout = QVBoxLayout(assigned_group)
        self.assigned_table = QTableWidget(0, 6)
        self.assigned_table.setHorizontalHeaderLabels(
            ["Sicil", "Personel", "Kayıtlı Rota", "Rota Uzaklığı", "Biniş Noktası", "Atama Tarihi"]
        )
        self.assigned_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.assigned_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.assigned_table.setAlternatingRowColors(True)
        self.assigned_table.horizontalHeader().setStretchLastSection(True)
        self.assigned_table.setMinimumHeight(150)
        assigned_layout.addWidget(self.assigned_table)
        self.assigned_count_label = QLabel("Atanan personel: 0")
        assigned_layout.addWidget(self.assigned_count_label)
        root.addWidget(assigned_group)

    def _connect_signals(self) -> None:
        self.filter_service_combo.currentIndexChanged.connect(self.refresh_list)
        self.refresh_button.clicked.connect(self.refresh_data)
        self.shift_table.itemSelectionChanged.connect(self._load_selected_row)
        self.new_button.clicked.connect(self._start_new_record)
        self.save_button.clicked.connect(self._save_new_record)
        self.update_button.clicked.connect(self._update_record)
        self.clear_button.clicked.connect(self._clear_form)
        self.toggle_active_button.clicked.connect(self._toggle_active)

    def refresh_data(self) -> None:
        selected_shift_id = self._selected_shift_id
        filter_id = self.filter_service_combo.currentData()
        form_service_id = self.service_combo.currentData()
        try:
            services = self.service.list_services()
            self.filter_service_combo.blockSignals(True)
            self.filter_service_combo.clear()
            self.filter_service_combo.addItem("Tüm Servisler", None)
            self.service_combo.clear()
            for item in services:
                status = "" if item.active else " (Pasif)"
                label = item.service_code + status
                self.filter_service_combo.addItem(label, item.id)
                self.service_combo.addItem(label, item.id)
            filter_index = self.filter_service_combo.findData(filter_id)
            self.filter_service_combo.setCurrentIndex(
                filter_index if filter_index >= 0 else 0
            )
            service_index = self.service_combo.findData(form_service_id)
            if service_index >= 0:
                self.service_combo.setCurrentIndex(service_index)
            self.filter_service_combo.blockSignals(False)
            self.refresh_list()
            if selected_shift_id is not None:
                self._select_shift(selected_shift_id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Servis vardiyası verileri yenilenemedi.")
            self._show_error("Servis vardiyası listesi yenilenemedi.")

    def refresh_list(self) -> None:
        try:
            records = self.service.list_service_shifts(
                self.filter_service_combo.currentData()
            )
            self._populate_table(records)
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _populate_table(self, records: tuple[ServiceShiftView, ...]) -> None:
        self.shift_table.blockSignals(True)
        self.shift_table.clearContents()
        self.shift_table.setRowCount(len(records))
        for row, shift in enumerate(records):
            values = (
                shift.service_code,
                shift.shift,
                shift.collar,
                str(shift.capacity),
                f"{shift.active_assignment_count}/{shift.capacity}",
                shift.route_name or "",
                "Aktif" if shift.active else "Pasif",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, shift.id)
                self.shift_table.setItem(row, column, item)
        self.shift_table.resizeColumnsToContents()
        self.shift_table.blockSignals(False)
        self.count_label.setText(f"Toplam: {len(records)}")

    def _load_selected_row(self) -> None:
        rows = self.shift_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.shift_table.item(rows[0].row(), 0)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        try:
            self._load_shift(
                self.service.get_service_shift(
                    int(item.data(Qt.ItemDataRole.UserRole))
                )
            )
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _load_shift(self, shift: ServiceShiftView) -> None:
        self._selected_shift_id = shift.id
        self._selected_active = shift.active
        self._new_record_mode = False
        self._set_combo_value(self.service_combo, shift.service_id)
        self.service_combo.setEnabled(False)
        self._set_combo_value(self.shift_combo, shift.shift)
        self._set_combo_value(self.collar_combo, shift.collar)
        self.capacity_spin.setValue(shift.capacity)
        self.occupancy_label.setText(
            f"{shift.active_assignment_count}/{shift.capacity} "
            f"(%{(100 * shift.active_assignment_count / shift.capacity):.1f})"
        )
        self.route_name_edit.setText(shift.route_name or "")
        self.active_check.setChecked(shift.active)
        self._refresh_assigned_personnel(shift.id)
        self._update_button_states()

    def _refresh_assigned_personnel(self, shift_id: int) -> None:
        records = self.service.list_assigned_personnel(shift_id)
        self.assigned_table.setRowCount(len(records))
        for row, person in enumerate(records):
            pickup = (
                "-"
                if person.pickup_latitude is None or person.pickup_longitude is None
                else f"{person.pickup_latitude:.6f}, {person.pickup_longitude:.6f}"
            )
            values = (
                person.personnel_number,
                person.personnel_name,
                person.route_name or "-",
                "-" if person.distance_meters is None else f"{person.distance_meters:.0f} m",
                pickup,
                person.assignment_date.strftime("%d.%m.%Y %H:%M"),
            )
            for column, value in enumerate(values):
                self.assigned_table.setItem(row, column, QTableWidgetItem(value))
        self.assigned_table.resizeColumnsToContents()
        self.assigned_count_label.setText(f"Atanan personel: {len(records)}")

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _start_new_record(self) -> None:
        if self.service_combo.count() == 0:
            QMessageBox.warning(
                self,
                "Servis-Vardiya Kaydı",
                "Önce bir fiziksel servis oluşturmalısınız.",
            )
            return
        selected_service = self.filter_service_combo.currentData()
        self.shift_table.clearSelection()
        self._clear_form()
        self._new_record_mode = True
        self.service_combo.setEnabled(True)
        if selected_service is not None:
            self._set_combo_value(self.service_combo, selected_service)
        self._update_button_states()

    def _clear_form(self) -> None:
        self._selected_shift_id = None
        self._selected_active = None
        self._new_record_mode = False
        self.service_combo.setEnabled(True)
        if self.service_combo.count() > 0:
            self.service_combo.setCurrentIndex(0)
        self.shift_combo.setCurrentIndex(0)
        self.collar_combo.setCurrentIndex(0)
        self.capacity_spin.setValue(16)
        self.occupancy_label.setText("0")
        self.route_name_edit.clear()
        self.active_check.setChecked(True)
        if hasattr(self, "assigned_table"):
            self.assigned_table.setRowCount(0)
            self.assigned_count_label.setText("Atanan personel: 0")
        self._update_button_states()

    def _update_button_states(self) -> None:
        selected = self._selected_shift_id is not None
        self.save_button.setEnabled(self._new_record_mode and not selected)
        self.update_button.setEnabled(selected)
        self.toggle_active_button.setEnabled(selected)
        self.toggle_active_button.setText(
            "Pasif Yap" if selected and self._selected_active else "Aktif Yap"
        )

    def _form_data(self) -> ServiceShiftFormData:
        return ServiceShiftFormData(
            shift=str(self.shift_combo.currentData() or ""),
            collar=str(self.collar_combo.currentData() or ""),
            capacity=self.capacity_spin.value(),
            route_name=self.route_name_edit.text(),
            active=self.active_check.isChecked(),
        )

    def _save_new_record(self) -> None:
        if not self._new_record_mode:
            return
        service_id = self.service_combo.currentData()
        if service_id is None:
            self._show_error("Fiziksel servis seçilmelidir.")
            return
        try:
            created = self.service.create_service_shift(
                int(service_id),
                self._form_data(),
            )
            QMessageBox.information(
                self,
                "Servis-Vardiya Kaydı",
                "Servis vardiyası başarıyla kaydedildi.",
            )
            self.data_changed.emit()
            self._select_shift(created.id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Servis vardiyası formu beklenmeyen hatayla sonuçlandı.")
            self._show_error("Servis vardiyası kaydedilemedi.")

    def _update_record(self) -> None:
        if self._selected_shift_id is None:
            return
        try:
            updated = self.service.update_service_shift(
                self._selected_shift_id,
                self._form_data(),
            )
            QMessageBox.information(
                self,
                "Servis-Vardiya Güncelleme",
                "Servis vardiyası başarıyla güncellendi.",
            )
            self.data_changed.emit()
            self._select_shift(updated.id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Servis vardiyası güncellenemedi.")
            self._show_error("Servis vardiyası güncellenemedi.")

    def _toggle_active(self) -> None:
        if self._selected_shift_id is None:
            return
        target = not bool(self._selected_active)
        try:
            updated = self.service.set_service_shift_active(
                self._selected_shift_id,
                target,
            )
            QMessageBox.information(
                self,
                "Servis-Vardiya Aktiflik Durumu",
                "Servis vardiyası aktif duruma alındı."
                if target
                else "Servis vardiyası pasif duruma alındı.",
            )
            self.data_changed.emit()
            self._select_shift(updated.id)
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _select_shift(self, shift_id: int) -> None:
        for row in range(self.shift_table.rowCount()):
            item = self.shift_table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == shift_id:
                self.shift_table.selectRow(row)
                self.shift_table.scrollToItem(item)
                return

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Servis-Vardiya İşlemi Hatası", message)
