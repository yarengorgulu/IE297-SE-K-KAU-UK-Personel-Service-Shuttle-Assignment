"""Şoför listeleme, ekleme, güncelleme ve servis ilişkilendirme ekranı."""

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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services import (
    DriverFormData,
    DriverView,
    ServiceManagementError,
    ServiceManagementService,
)


logger = logging.getLogger(__name__)


class DriverManagementWidget(QWidget):
    data_changed = Signal()

    def __init__(
        self,
        parent=None,
        *,
        service: ServiceManagementService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or ServiceManagementService()
        self._selected_driver_id: int | None = None
        self._selected_active: bool | None = None
        self._new_record_mode = False
        self._build_ui()
        self._connect_signals()
        self.refresh_data()
        self._clear_form()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        top_buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Listeyi Yenile")
        top_buttons.addStretch()
        top_buttons.addWidget(self.refresh_button)
        root.addLayout(top_buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_group = QGroupBox("Şoför Listesi")
        list_layout = QVBoxLayout(list_group)
        self.driver_table = QTableWidget()
        self.driver_table.setColumnCount(4)
        self.driver_table.setHorizontalHeaderLabels(
            ["Ad Soyad", "Telefon", "İlişkili Servisler", "Durum"]
        )
        self.driver_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.driver_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.driver_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.driver_table.setAlternatingRowColors(True)
        self.driver_table.horizontalHeader().setStretchLastSection(True)
        list_layout.addWidget(self.driver_table, 1)
        self.count_label = QLabel("Toplam: 0")
        list_layout.addWidget(self.count_label)
        splitter.addWidget(list_group)

        form_group = QGroupBox("Şoför Bilgileri")
        form_root = QVBoxLayout(form_group)
        form = QFormLayout()
        self.first_name_edit = QLineEdit()
        self.last_name_edit = QLineEdit()
        self.phone_edit = QLineEdit()
        self.active_check = QCheckBox("Şoför aktif")
        self.link_service_combo = QComboBox()
        self.linked_services_label = QLabel("-")
        self.linked_services_label.setWordWrap(True)
        form.addRow("Ad*:", self.first_name_edit)
        form.addRow("Soyad*:", self.last_name_edit)
        form.addRow("Telefon:", self.phone_edit)
        form.addRow("Aktiflik:", self.active_check)
        form.addRow("Servisle İlişkilendir:", self.link_service_combo)
        form.addRow("Mevcut Servisler:", self.linked_services_label)
        form_root.addLayout(form)

        note = QLabel(
            "Servis seçildiğinde ilişki Kaydet veya Güncelle işlemiyle kurulur. "
            "Şoförün aynı vardiyada başka aktif servisi varsa işlem engellenir."
        )
        note.setWordWrap(True)
        form_root.addWidget(note)

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
        splitter.setSizes([680, 420])
        root.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_data)
        self.driver_table.itemSelectionChanged.connect(self._load_selected_row)
        self.new_button.clicked.connect(self._start_new_record)
        self.save_button.clicked.connect(self._save_new_record)
        self.update_button.clicked.connect(self._update_record)
        self.clear_button.clicked.connect(self._clear_form)
        self.toggle_active_button.clicked.connect(self._toggle_active)

    def refresh_data(self) -> None:
        selected_driver_id = self._selected_driver_id
        linked_service_id = self.link_service_combo.currentData()
        try:
            self.link_service_combo.clear()
            self.link_service_combo.addItem("Yeni ilişki kurma", None)
            for service in self.service.list_services():
                status = "" if service.active else " (Pasif)"
                self.link_service_combo.addItem(
                    service.service_code + status,
                    service.id,
                )
            index = self.link_service_combo.findData(linked_service_id)
            self.link_service_combo.setCurrentIndex(index if index >= 0 else 0)
            self._populate_table(self.service.list_drivers())
            if selected_driver_id is not None:
                self._select_driver(selected_driver_id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Şoför yönetimi verileri yenilenemedi.")
            self._show_error("Şoför listesi yenilenemedi.")

    def _populate_table(self, records: tuple[DriverView, ...]) -> None:
        self.driver_table.blockSignals(True)
        self.driver_table.clearContents()
        self.driver_table.setRowCount(len(records))
        for row, driver in enumerate(records):
            values = (
                driver.full_name,
                driver.phone or "",
                ", ".join(driver.service_codes),
                "Aktif" if driver.active else "Pasif",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, driver.id)
                self.driver_table.setItem(row, column, item)
        self.driver_table.resizeColumnsToContents()
        self.driver_table.blockSignals(False)
        self.count_label.setText(f"Toplam: {len(records)}")

    def _load_selected_row(self) -> None:
        rows = self.driver_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.driver_table.item(rows[0].row(), 0)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        try:
            self._load_driver(
                self.service.get_driver(int(item.data(Qt.ItemDataRole.UserRole)))
            )
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _load_driver(self, driver: DriverView) -> None:
        self._selected_driver_id = driver.id
        self._selected_active = driver.active
        self._new_record_mode = False
        self.first_name_edit.setText(driver.first_name or "")
        self.last_name_edit.setText(driver.last_name or "")
        self.phone_edit.setText(driver.phone or "")
        self.active_check.setChecked(driver.active)
        self.link_service_combo.setCurrentIndex(0)
        self.linked_services_label.setText(
            ", ".join(driver.service_codes) if driver.service_codes else "-"
        )
        self._update_button_states()

    def _start_new_record(self) -> None:
        self.driver_table.clearSelection()
        self._clear_form()
        self._new_record_mode = True
        self._update_button_states()
        self.first_name_edit.setFocus()

    def _clear_form(self) -> None:
        self._selected_driver_id = None
        self._selected_active = None
        self._new_record_mode = False
        self.first_name_edit.clear()
        self.last_name_edit.clear()
        self.phone_edit.clear()
        self.active_check.setChecked(True)
        self.link_service_combo.setCurrentIndex(0)
        self.linked_services_label.setText("-")
        self._update_button_states()

    def _update_button_states(self) -> None:
        selected = self._selected_driver_id is not None
        self.save_button.setEnabled(self._new_record_mode and not selected)
        self.update_button.setEnabled(selected)
        self.toggle_active_button.setEnabled(selected)
        self.toggle_active_button.setText(
            "Pasif Yap" if selected and self._selected_active else "Aktif Yap"
        )

    def _form_data(self) -> DriverFormData:
        return DriverFormData(
            first_name=self.first_name_edit.text(),
            last_name=self.last_name_edit.text(),
            phone=self.phone_edit.text(),
            active=self.active_check.isChecked(),
            linked_service_id=self.link_service_combo.currentData(),
        )

    def _save_new_record(self) -> None:
        if not self._new_record_mode:
            return
        try:
            created = self.service.create_driver(self._form_data())
            QMessageBox.information(
                self,
                "Şoför Kaydı",
                "Şoför bilgileri başarıyla kaydedildi.",
            )
            self.data_changed.emit()
            self._select_driver(created.id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Şoför formu beklenmeyen hatayla sonuçlandı.")
            self._show_error("Şoför kaydı tamamlanamadı.")

    def _update_record(self) -> None:
        if self._selected_driver_id is None:
            return
        try:
            updated = self.service.update_driver(
                self._selected_driver_id,
                self._form_data(),
            )
            QMessageBox.information(
                self,
                "Şoför Güncelleme",
                "Şoför bilgileri başarıyla güncellendi.",
            )
            self.data_changed.emit()
            self._select_driver(updated.id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Şoför güncelleme arayüzde tamamlanamadı.")
            self._show_error("Şoför bilgileri güncellenemedi.")

    def _toggle_active(self) -> None:
        if self._selected_driver_id is None:
            return
        target = not bool(self._selected_active)
        try:
            updated = self.service.set_driver_active(self._selected_driver_id, target)
            QMessageBox.information(
                self,
                "Şoför Aktiflik Durumu",
                "Şoför aktif duruma alındı."
                if target
                else "Şoför pasif duruma alındı.",
            )
            self.data_changed.emit()
            self._select_driver(updated.id)
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _select_driver(self, driver_id: int) -> None:
        for row in range(self.driver_table.rowCount()):
            item = self.driver_table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == driver_id:
                self.driver_table.selectRow(row)
                self.driver_table.scrollToItem(item)
                return

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Şoför İşlemi Hatası", message)
