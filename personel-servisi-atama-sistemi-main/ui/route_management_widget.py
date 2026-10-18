"""Servis-vardiya güzergâh noktaları manuel yönetim ekranı."""

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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services import (
    RouteManagementError,
    RouteManagementService,
    RoutePointFormData,
    RoutePointView,
    ServiceManagementError,
    ServiceManagementService,
)


logger = logging.getLogger(__name__)


class RouteManagementWidget(QWidget):
    data_changed = Signal()

    def __init__(
        self,
        parent=None,
        *,
        service_management: ServiceManagementService | None = None,
        route_service: RouteManagementService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service_management = service_management or ServiceManagementService()
        self.route_service = route_service or RouteManagementService(
            self.service_management.session_factory
        )
        self._selected_point_id: int | None = None
        self._selected_active: bool | None = None
        self._new_record_mode = False
        self._build_ui()
        self._connect_signals()
        self.refresh_data()
        self._clear_form()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        selection = QHBoxLayout()
        self.service_combo = QComboBox()
        self.shift_combo = QComboBox()
        self.refresh_button = QPushButton("Listeyi Yenile")
        selection.addWidget(QLabel("Fiziksel Servis:"))
        selection.addWidget(self.service_combo, 1)
        selection.addWidget(QLabel("Servis-Vardiya:"))
        selection.addWidget(self.shift_combo, 2)
        selection.addWidget(self.refresh_button)
        root.addLayout(selection)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_group = QGroupBox("Sıralı Güzergâh Noktaları")
        list_layout = QVBoxLayout(list_group)
        self.point_table = QTableWidget()
        self.point_table.setColumnCount(9)
        self.point_table.setHorizontalHeaderLabels(
            [
                "Sıra",
                "Konum",
                "İl",
                "İlçe",
                "Mahalle",
                "Sokak",
                "Cadde",
                "Nokta/Durak",
                "Durum",
            ]
        )
        self.point_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.point_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.point_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.point_table.setAlternatingRowColors(True)
        self.point_table.horizontalHeader().setStretchLastSection(True)
        list_layout.addWidget(self.point_table, 1)
        self.count_label = QLabel("Toplam: 0")
        list_layout.addWidget(self.count_label)
        splitter.addWidget(list_group)

        form_group = QGroupBox("Güzergâh Noktası Bilgileri")
        form_root = QVBoxLayout(form_group)
        form = QFormLayout()
        self.sequence_spin = QSpinBox()
        self.sequence_spin.setRange(1, 1000000)
        self.city_edit = QLineEdit()
        self.district_edit = QLineEdit()
        self.neighborhood_edit = QLineEdit()
        self.street_edit = QLineEdit()
        self.avenue_edit = QLineEdit()
        self.point_name_edit = QLineEdit()
        self.address_edit = QLineEdit()
        self.description_edit = QTextEdit()
        self.description_edit.setFixedHeight(70)
        self.latitude_edit = QLineEdit()
        self.longitude_edit = QLineEdit()
        self.latitude_edit.setPlaceholderText("İsteğe bağlı")
        self.longitude_edit.setPlaceholderText("İsteğe bağlı")
        self.active_check = QCheckBox("Güzergâh noktası aktif")
        form.addRow("Sıra No*:", self.sequence_spin)
        form.addRow("Mahalle:", self.neighborhood_edit)
        form.addRow("Sokak:", self.street_edit)
        form.addRow("Cadde:", self.avenue_edit)
        form.addRow("İlçe:", self.district_edit)
        form.addRow("İl:", self.city_edit)
        form.addRow("Nokta/Durak Adı:", self.point_name_edit)
        form.addRow("Açık Adres:", self.address_edit)
        form.addRow("Açıklama:", self.description_edit)
        form.addRow("Enlem:", self.latitude_edit)
        form.addRow("Boylam:", self.longitude_edit)
        form.addRow("Aktiflik:", self.active_check)
        form_root.addLayout(form)

        primary_buttons = QHBoxLayout()
        secondary_buttons = QHBoxLayout()
        order_buttons = QHBoxLayout()
        self.new_button = QPushButton("Yeni Kayıt")
        self.save_button = QPushButton("Kaydet")
        self.update_button = QPushButton("Güncelle")
        self.clear_button = QPushButton("Temizle")
        self.toggle_active_button = QPushButton("Aktif/Pasif")
        self.move_up_button = QPushButton("Yukarı Taşı")
        self.move_down_button = QPushButton("Aşağı Taşı")
        for button in (self.new_button, self.save_button, self.update_button):
            primary_buttons.addWidget(button)
        for button in (self.clear_button, self.toggle_active_button):
            secondary_buttons.addWidget(button)
        order_buttons.addWidget(self.move_up_button)
        order_buttons.addWidget(self.move_down_button)
        form_root.addLayout(primary_buttons)
        form_root.addLayout(secondary_buttons)
        form_root.addLayout(order_buttons)
        form_root.addStretch()
        splitter.addWidget(form_group)
        splitter.setSizes([700, 430])
        root.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.service_combo.currentIndexChanged.connect(self._service_changed)
        self.shift_combo.currentIndexChanged.connect(self._shift_changed)
        self.refresh_button.clicked.connect(self.refresh_data)
        self.point_table.itemSelectionChanged.connect(self._load_selected_row)
        self.new_button.clicked.connect(self._start_new_record)
        self.save_button.clicked.connect(self._save_new_record)
        self.update_button.clicked.connect(self._update_record)
        self.clear_button.clicked.connect(self._clear_form)
        self.toggle_active_button.clicked.connect(self._toggle_active)
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))

    def refresh_data(self) -> None:
        selected_service_id = self.service_combo.currentData()
        selected_shift_id = self.shift_combo.currentData()
        try:
            self.service_combo.blockSignals(True)
            self.service_combo.clear()
            for service in self.service_management.list_services():
                status = "" if service.active else " (Pasif)"
                self.service_combo.addItem(service.service_code + status, service.id)
            self._set_combo_value(self.service_combo, selected_service_id)
            self.service_combo.blockSignals(False)
            self._populate_shift_combo(selected_shift_id)
            self.refresh_list()
        except (ServiceManagementError, RouteManagementError) as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Güzergâh yönetimi verileri yenilenemedi.")
            self._show_error("Güzergâh yönetimi verileri yenilenemedi.")

    def _service_changed(self) -> None:
        self._selected_point_id = None
        self._populate_shift_combo()
        self._clear_form()
        self.refresh_list()

    def _populate_shift_combo(self, selected_shift_id: object = None) -> None:
        service_id = self.service_combo.currentData()
        self.shift_combo.blockSignals(True)
        self.shift_combo.clear()
        if service_id is not None:
            for shift in self.service_management.list_service_shifts(int(service_id)):
                status = "" if shift.active else " (Pasif)"
                label = f"{shift.shift} / {shift.collar} / {shift.route_name or 'Rota adı yok'}{status}"
                self.shift_combo.addItem(label, shift.id)
        self._set_combo_value(self.shift_combo, selected_shift_id)
        self.shift_combo.blockSignals(False)

    def _shift_changed(self) -> None:
        self._selected_point_id = None
        self._clear_form()
        self.refresh_list()

    def refresh_list(self) -> None:
        shift_id = self.shift_combo.currentData()
        if shift_id is None:
            self._populate_table(())
            self.new_button.setEnabled(False)
            return
        try:
            self._populate_table(self.route_service.list_points(int(shift_id)))
            self.new_button.setEnabled(True)
        except RouteManagementError as error:
            self._show_error(str(error))

    def _populate_table(self, records: tuple[RoutePointView, ...]) -> None:
        self.point_table.blockSignals(True)
        self.point_table.clearContents()
        self.point_table.setRowCount(len(records))
        for row, point in enumerate(records):
            values = (
                str(point.sequence),
                point.position_label,
                point.city or "",
                point.district or "",
                point.neighborhood or "",
                point.street or "",
                point.avenue or "",
                point.point_name or "",
                "Aktif" if point.active else "Pasif",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, point.id)
                self.point_table.setItem(row, column, item)
        self.point_table.resizeColumnsToContents()
        self.point_table.blockSignals(False)
        self.count_label.setText(f"Toplam: {len(records)}")

    def _load_selected_row(self) -> None:
        rows = self.point_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.point_table.item(rows[0].row(), 0)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        try:
            self._load_point(
                self.route_service.get_point(int(item.data(Qt.ItemDataRole.UserRole)))
            )
        except RouteManagementError as error:
            self._show_error(str(error))

    def _load_point(self, point: RoutePointView) -> None:
        self._selected_point_id = point.id
        self._selected_active = point.active
        self._new_record_mode = False
        self.sequence_spin.setValue(point.sequence)
        self.city_edit.setText(point.city or "")
        self.district_edit.setText(point.district or "")
        self.neighborhood_edit.setText(point.neighborhood or "")
        self.street_edit.setText(point.street or "")
        self.avenue_edit.setText(point.avenue or "")
        self.point_name_edit.setText(point.point_name or "")
        self.address_edit.setText(point.address or "")
        self.description_edit.setPlainText(point.description or "")
        self.latitude_edit.setText("" if point.latitude is None else str(point.latitude))
        self.longitude_edit.setText(
            "" if point.longitude is None else str(point.longitude)
        )
        self.active_check.setChecked(point.active)
        self._update_button_states()

    def _start_new_record(self) -> None:
        shift_id = self.shift_combo.currentData()
        if shift_id is None:
            QMessageBox.warning(
                self,
                "Güzergâh Noktası",
                "Önce fiziksel servis ve servis-vardiya seçmelisiniz.",
            )
            return
        self.point_table.clearSelection()
        self._clear_form()
        self._new_record_mode = True
        try:
            self.sequence_spin.setValue(
                self.route_service.next_sequence(int(shift_id))
            )
        except RouteManagementError as error:
            self._show_error(str(error))
            return
        self._update_button_states()
        self.district_edit.setFocus()

    def _clear_form(self) -> None:
        self._selected_point_id = None
        self._selected_active = None
        self._new_record_mode = False
        self.sequence_spin.setValue(1)
        self.city_edit.clear()
        self.district_edit.clear()
        self.neighborhood_edit.clear()
        self.street_edit.clear()
        self.avenue_edit.clear()
        self.point_name_edit.clear()
        self.address_edit.clear()
        self.description_edit.clear()
        self.latitude_edit.clear()
        self.longitude_edit.clear()
        self.active_check.setChecked(True)
        self._update_button_states()

    def _update_button_states(self) -> None:
        selected = self._selected_point_id is not None
        shift_selected = self.shift_combo.currentData() is not None
        self.new_button.setEnabled(shift_selected)
        self.save_button.setEnabled(self._new_record_mode and not selected)
        self.update_button.setEnabled(selected)
        self.toggle_active_button.setEnabled(selected)
        self.move_up_button.setEnabled(selected)
        self.move_down_button.setEnabled(selected)
        self.toggle_active_button.setText(
            "Pasif Yap" if selected and self._selected_active else "Aktif Yap"
        )

    def _form_data(self) -> RoutePointFormData:
        return RoutePointFormData(
            sequence=self.sequence_spin.value(),
            city=self.city_edit.text(),
            district=self.district_edit.text(),
            neighborhood=self.neighborhood_edit.text(),
            street=self.street_edit.text(),
            avenue=self.avenue_edit.text(),
            point_name=self.point_name_edit.text(),
            address=self.address_edit.text(),
            description=self.description_edit.toPlainText(),
            latitude=self.latitude_edit.text(),
            longitude=self.longitude_edit.text(),
            active=self.active_check.isChecked(),
        )

    def _save_new_record(self) -> None:
        shift_id = self.shift_combo.currentData()
        if not self._new_record_mode or shift_id is None:
            return
        try:
            created = self.route_service.create_point(
                int(shift_id),
                self._form_data(),
            )
            QMessageBox.information(
                self,
                "Güzergâh Noktası",
                "Güzergâh noktası başarıyla kaydedildi.",
            )
            self.data_changed.emit()
            self._select_point(created.id)
        except RouteManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Güzergâh noktası formu beklenmeyen hatayla sonuçlandı.")
            self._show_error("Güzergâh noktası kaydedilemedi.")

    def _update_record(self) -> None:
        if self._selected_point_id is None:
            return
        try:
            updated = self.route_service.update_point(
                self._selected_point_id,
                self._form_data(),
            )
            QMessageBox.information(
                self,
                "Güzergâh Güncelleme",
                "Güzergâh noktası başarıyla güncellendi.",
            )
            self.data_changed.emit()
            self._select_point(updated.id)
        except RouteManagementError as error:
            self._show_error(str(error))

    def _toggle_active(self) -> None:
        if self._selected_point_id is None:
            return
        target = not bool(self._selected_active)
        try:
            updated = self.route_service.set_point_active(
                self._selected_point_id,
                target,
            )
            QMessageBox.information(
                self,
                "Güzergâh Aktiflik Durumu",
                "Güzergâh noktası aktif duruma alındı."
                if target
                else "Güzergâh noktası pasif duruma alındı.",
            )
            self.data_changed.emit()
            self._select_point(updated.id)
        except RouteManagementError as error:
            self._show_error(str(error))

    def _move_selected(self, direction: int) -> None:
        if self._selected_point_id is None:
            return
        try:
            result = self.route_service.move_point(self._selected_point_id, direction)
            if not result.moved:
                QMessageBox.information(
                    self,
                    "Güzergâh Sıralaması",
                    "Nokta bu yönde daha fazla taşınamaz.",
                )
            self.data_changed.emit()
            self._select_point(result.point.id)
        except RouteManagementError as error:
            self._show_error(str(error))

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _select_point(self, point_id: int) -> None:
        for row in range(self.point_table.rowCount()):
            item = self.point_table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == point_id:
                self.point_table.selectRow(row)
                self.point_table.scrollToItem(item)
                return

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Güzergâh İşlemi Hatası", message)
