"""Fiziksel servis listeleme ve manuel kayıt ekranı."""

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
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.models import VardiyaTuru, YakaTuru
from services import (
    ServiceFormData,
    ServiceManagementError,
    ServiceManagementService,
    ServiceShiftFormData,
    ServiceView,
)


logger = logging.getLogger(__name__)


class ServiceListManagementWidget(QWidget):
    data_changed = Signal()

    def __init__(
        self,
        parent=None,
        *,
        service: ServiceManagementService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or ServiceManagementService()
        self._selected_service_id: int | None = None
        self._selected_active: bool | None = None
        self._new_record_mode = False
        self._build_ui()
        self._connect_signals()
        self.refresh_data()
        self._clear_form()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        list_group = QGroupBox("Servis Listesi")
        list_layout = QVBoxLayout(list_group)
        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Servis kodu girin")
        self.search_button = QPushButton("Ara")
        self.clear_search_button = QPushButton("Aramayı Temizle")
        self.refresh_button = QPushButton("Listeyi Yenile")
        search_layout.addWidget(QLabel("Servis Kodu:"))
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(self.search_button)
        search_layout.addWidget(self.clear_search_button)
        search_layout.addWidget(self.refresh_button)
        list_layout.addLayout(search_layout)

        self.service_table = QTableWidget()
        self.service_table.setColumnCount(4)
        self.service_table.setHorizontalHeaderLabels(
            ["Servis Kodu", "Şoför", "Vardiya Sayısı", "Durum"]
        )
        self.service_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.service_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.service_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.service_table.setAlternatingRowColors(True)
        self.service_table.horizontalHeader().setStretchLastSection(True)
        list_layout.addWidget(self.service_table, 1)
        self.count_label = QLabel("Toplam: 0")
        list_layout.addWidget(self.count_label)
        splitter.addWidget(list_group)

        form_group = QGroupBox("Fiziksel Servis Bilgileri")
        form_root = QVBoxLayout(form_group)
        form = QFormLayout()
        self.service_code_edit = QLineEdit()
        self.driver_combo = QComboBox()
        self.active_check = QCheckBox("Fiziksel servis aktif")
        form.addRow("Servis Kodu*:", self.service_code_edit)
        form.addRow("Şoför:", self.driver_combo)
        form.addRow("Aktiflik:", self.active_check)
        form_root.addLayout(form)

        first_shift_group = QGroupBox("İlk Servis-Vardiya Kaydı")
        first_shift_layout = QFormLayout(first_shift_group)
        self.create_first_shift_check = QCheckBox(
            "Servisle birlikte ilk vardiyayı oluştur"
        )
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
        self.first_shift_active_check = QCheckBox("İlk vardiya aktif")
        first_shift_layout.addRow(self.create_first_shift_check)
        first_shift_layout.addRow("Vardiya*:", self.shift_combo)
        first_shift_layout.addRow("Yaka*:", self.collar_combo)
        first_shift_layout.addRow("Kapasite*:", self.capacity_spin)
        first_shift_layout.addRow("Rota Adı:", self.route_name_edit)
        first_shift_layout.addRow("Aktiflik:", self.first_shift_active_check)
        form_root.addWidget(first_shift_group)

        primary_buttons = QHBoxLayout()
        secondary_buttons = QHBoxLayout()
        self.new_button = QPushButton("Yeni Kayıt")
        self.save_button = QPushButton("Kaydet")
        self.update_button = QPushButton("Güncelle")
        self.clear_button = QPushButton("Temizle")
        self.toggle_active_button = QPushButton("Aktif/Pasif")
        self.delete_button = QPushButton("Kontrollü Sil")
        for button in (self.new_button, self.save_button, self.update_button):
            primary_buttons.addWidget(button)
        for button in (self.clear_button, self.toggle_active_button, self.delete_button):
            secondary_buttons.addWidget(button)
        form_root.addLayout(primary_buttons)
        form_root.addLayout(secondary_buttons)
        form_root.addStretch()

        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        form_scroll.setWidget(form_group)
        splitter.addWidget(form_scroll)
        splitter.setSizes([620, 480])
        root.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.search_button.clicked.connect(self._search)
        self.search_edit.returnPressed.connect(self._search)
        self.clear_search_button.clicked.connect(self._clear_search)
        self.refresh_button.clicked.connect(self.refresh_data)
        self.service_table.itemSelectionChanged.connect(self._load_selected_row)
        self.new_button.clicked.connect(self._start_new_record)
        self.save_button.clicked.connect(self._save_new_record)
        self.update_button.clicked.connect(self._update_record)
        self.clear_button.clicked.connect(self._clear_form)
        self.toggle_active_button.clicked.connect(self._toggle_active)
        self.delete_button.clicked.connect(self._delete_service)
        self.create_first_shift_check.toggled.connect(
            self._update_first_shift_fields
        )

    def refresh_data(self) -> None:
        selected_id = self._selected_service_id
        try:
            self._refresh_driver_combo()
            self._populate_table(self.service.list_services(self.search_edit.text()))
            if selected_id is not None:
                self._select_service(selected_id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Servis yönetimi verileri yenilenemedi.")
            self._show_error("Servis listesi yenilenemedi.")

    def _refresh_driver_combo(self) -> None:
        selected = self.driver_combo.currentData()
        self.driver_combo.blockSignals(True)
        self.driver_combo.clear()
        self.driver_combo.addItem("Şoför atanmadı", None)
        for driver in self.service.list_drivers():
            status = "" if driver.active else " (Pasif)"
            self.driver_combo.addItem(driver.full_name + status, driver.id)
        index = self.driver_combo.findData(selected)
        self.driver_combo.setCurrentIndex(index if index >= 0 else 0)
        self.driver_combo.blockSignals(False)

    def _search(self) -> None:
        try:
            records = self.service.list_services(self.search_edit.text())
            self._populate_table(records)
            exact = next(
                (
                    item
                    for item in records
                    if item.service_code == self.search_edit.text().strip().upper()
                ),
                None,
            )
            if exact is not None:
                self._select_service(exact.id)
            elif not records:
                self._clear_form()
                QMessageBox.information(
                    self,
                    "Servis Arama",
                    "Bu servis koduyla eşleşen kayıt bulunamadı.",
                )
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _clear_search(self) -> None:
        self.search_edit.clear()
        self.refresh_data()

    def _populate_table(self, records: tuple[ServiceView, ...]) -> None:
        self.service_table.blockSignals(True)
        self.service_table.clearContents()
        self.service_table.setRowCount(len(records))
        for row, service in enumerate(records):
            values = (
                service.service_code,
                service.driver_name or "",
                str(service.shift_count),
                "Aktif" if service.active else "Pasif",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, service.id)
                self.service_table.setItem(row, column, item)
        self.service_table.resizeColumnsToContents()
        self.service_table.blockSignals(False)
        self.count_label.setText(f"Toplam: {len(records)}")

    def _load_selected_row(self) -> None:
        rows = self.service_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.service_table.item(rows[0].row(), 0)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        try:
            self._load_service(
                self.service.get_service(int(item.data(Qt.ItemDataRole.UserRole)))
            )
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _load_service(self, service: ServiceView) -> None:
        self._selected_service_id = service.id
        self._selected_active = service.active
        self._new_record_mode = False
        self.service_code_edit.setText(service.service_code)
        self.service_code_edit.setReadOnly(True)
        index = self.driver_combo.findData(service.driver_id)
        self.driver_combo.setCurrentIndex(index if index >= 0 else 0)
        self.active_check.setChecked(service.active)
        self.create_first_shift_check.setChecked(False)
        self.create_first_shift_check.setEnabled(False)
        self._update_button_states()

    def _start_new_record(self) -> None:
        self.service_table.clearSelection()
        self._clear_form()
        self._new_record_mode = True
        self.create_first_shift_check.setEnabled(True)
        self.create_first_shift_check.setChecked(True)
        self._update_button_states()
        self.service_code_edit.setFocus()

    def _clear_form(self) -> None:
        self._selected_service_id = None
        self._selected_active = None
        self._new_record_mode = False
        self.service_code_edit.clear()
        self.service_code_edit.setReadOnly(False)
        self.driver_combo.setCurrentIndex(0)
        self.active_check.setChecked(True)
        self.create_first_shift_check.setEnabled(True)
        self.create_first_shift_check.setChecked(False)
        self.shift_combo.setCurrentIndex(0)
        self.collar_combo.setCurrentIndex(0)
        self.capacity_spin.setValue(16)
        self.route_name_edit.clear()
        self.first_shift_active_check.setChecked(True)
        self._update_first_shift_fields()
        self._update_button_states()

    def _update_first_shift_fields(self) -> None:
        enabled = self.create_first_shift_check.isChecked() and self._new_record_mode
        for widget in (
            self.shift_combo,
            self.collar_combo,
            self.capacity_spin,
            self.route_name_edit,
            self.first_shift_active_check,
        ):
            widget.setEnabled(enabled)

    def _update_button_states(self) -> None:
        selected = self._selected_service_id is not None
        self.save_button.setEnabled(self._new_record_mode and not selected)
        self.update_button.setEnabled(selected)
        self.toggle_active_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)
        self.toggle_active_button.setText(
            "Pasif Yap" if selected and self._selected_active else "Aktif Yap"
        )
        self._update_first_shift_fields()

    def _service_form_data(self) -> ServiceFormData:
        return ServiceFormData(
            service_code=self.service_code_edit.text(),
            driver_id=self.driver_combo.currentData(),
            active=self.active_check.isChecked(),
        )

    def _first_shift_form_data(self) -> ServiceShiftFormData | None:
        if not self.create_first_shift_check.isChecked():
            return None
        return ServiceShiftFormData(
            shift=str(self.shift_combo.currentData() or ""),
            collar=str(self.collar_combo.currentData() or ""),
            capacity=self.capacity_spin.value(),
            route_name=self.route_name_edit.text(),
            active=self.first_shift_active_check.isChecked(),
        )

    def _save_new_record(self) -> None:
        if not self._new_record_mode:
            return
        try:
            created = self.service.create_service(
                self._service_form_data(),
                self._first_shift_form_data(),
            )
            QMessageBox.information(
                self,
                "Servis Kaydı",
                "Fiziksel servis ve seçilen ilk vardiya başarıyla kaydedildi."
                if self.create_first_shift_check.isChecked()
                else "Fiziksel servis başarıyla kaydedildi.",
            )
            self.data_changed.emit()
            self._select_service(created.id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Servis formu beklenmeyen hatayla sonuçlandı.")
            self._show_error("Servis kaydı tamamlanamadı.")

    def _update_record(self) -> None:
        if self._selected_service_id is None:
            return
        try:
            updated = self.service.update_service(
                self._selected_service_id,
                self._service_form_data(),
            )
            QMessageBox.information(
                self,
                "Servis Güncelleme",
                "Fiziksel servis bilgileri başarıyla güncellendi.",
            )
            self.data_changed.emit()
            self._select_service(updated.id)
        except ServiceManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Servis güncelleme arayüzde tamamlanamadı.")
            self._show_error("Servis bilgileri güncellenemedi.")

    def _toggle_active(self) -> None:
        if self._selected_service_id is None:
            return
        target = not bool(self._selected_active)
        try:
            updated = self.service.set_service_active(self._selected_service_id, target)
            QMessageBox.information(
                self,
                "Servis Aktiflik Durumu",
                "Fiziksel servis aktif duruma alındı."
                if target
                else "Fiziksel servis pasif duruma alındı.",
            )
            self.data_changed.emit()
            self._select_service(updated.id)
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _delete_service(self) -> None:
        if self._selected_service_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Kontrollü Servis Silme",
            "Kullanılmamış servis kalıcı silinir; atama/geçmiş ilişkisi bulunan "
            "servis ve vardiyaları yalnızca pasif yapılır. Devam edilsin mi?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.service.delete_service_controlled(self._selected_service_id)
            QMessageBox.information(self, "Servis Silme", result.message)
            self._clear_form()
            self.refresh_data()
            self.data_changed.emit()
        except ServiceManagementError as error:
            self._show_error(str(error))

    def _select_service(self, service_id: int) -> None:
        for row in range(self.service_table.rowCount()):
            item = self.service_table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == service_id:
                self.service_table.selectRow(row)
                self.service_table.scrollToItem(item)
                return

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Servis İşlemi Hatası", message)
