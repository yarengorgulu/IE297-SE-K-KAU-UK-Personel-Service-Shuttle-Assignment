"""Personel listeleme, arama, manuel ekleme ve güncelleme ekranı."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from database.models import PersonelTuru, VardiyaTuru, YakaTuru
from services import (
    PersonnelFormData,
    PersonnelManagementError,
    PersonnelManagementService,
    PersonnelSaveResult,
    PersonnelView,
    PersonnelWarningsRequireConfirmation,
)


logger = logging.getLogger(__name__)


class PersonnelManagementWidget(QWidget):
    REASSESSMENT_MESSAGE = (
        "Personelin servis atamasını etkileyebilecek bilgiler değişti. "
        "İleride yeniden değerlendirme gerekebilir."
    )

    def __init__(
        self,
        parent=None,
        *,
        service: PersonnelManagementService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or PersonnelManagementService()
        self._selected_personnel_id: int | None = None
        self._selected_active: bool | None = None
        self._new_record_mode = False
        self._build_ui()
        self._connect_signals()
        self.refresh_list()
        self._clear_form()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        list_group = QGroupBox("Personel Listesi")
        list_layout = QVBoxLayout(list_group)
        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Sicil numarası girin")
        self.search_button = QPushButton("Ara")
        self.clear_search_button = QPushButton("Aramayı Temizle")
        self.refresh_button = QPushButton("Listeyi Yenile")
        search_layout.addWidget(QLabel("Sicil No:"))
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(self.search_button)
        search_layout.addWidget(self.clear_search_button)
        search_layout.addWidget(self.refresh_button)
        list_layout.addLayout(search_layout)

        self.personnel_table = QTableWidget()
        self.personnel_table.setColumnCount(6)
        self.personnel_table.setHorizontalHeaderLabels(
            ["Sicil No", "Ad", "Soyad", "Vardiya", "Yaka", "Durum"]
        )
        self.personnel_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.personnel_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.personnel_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.personnel_table.setAlternatingRowColors(True)
        self.personnel_table.horizontalHeader().setStretchLastSection(True)
        list_layout.addWidget(self.personnel_table, 1)
        self.list_count_label = QLabel("Toplam: 0")
        list_layout.addWidget(self.list_count_label)
        splitter.addWidget(list_group)

        form_group = QGroupBox("Personel Bilgileri")
        form_root = QVBoxLayout(form_group)
        form_layout = QFormLayout()
        self.personnel_number_edit = QLineEdit()
        self.first_name_edit = QLineEdit()
        self.last_name_edit = QLineEdit()
        self.address_edit = QTextEdit()
        self.address_edit.setFixedHeight(80)
        self.city_edit = QLineEdit()
        self.district_edit = QLineEdit()
        self.neighborhood_edit = QLineEdit()
        self.street_edit = QLineEdit()
        self.avenue_edit = QLineEdit()
        self.shift_combo = QComboBox()
        for shift in VardiyaTuru:
            self.shift_combo.addItem(shift.value, shift.value)
        self.collar_combo = QComboBox()
        for collar in YakaTuru:
            self.collar_combo.addItem(collar.value, collar.value)
        self.active_check = QCheckBox("Personel aktif")
        self.personnel_type_combo = QComboBox()
        for personnel_type in PersonelTuru:
            self.personnel_type_combo.addItem(
                personnel_type.value,
                personnel_type.value,
            )
        self.latitude_edit = QLineEdit()
        self.longitude_edit = QLineEdit()
        self.internship_start_edit = QLineEdit()
        self.internship_start_edit.setPlaceholderText("YYYY-AA-GG")
        self.internship_end_edit = QLineEdit()
        self.internship_end_edit.setPlaceholderText("YYYY-AA-GG")
        for read_only_field in (
            self.latitude_edit,
            self.longitude_edit,
        ):
            read_only_field.setReadOnly(True)

        form_layout.addRow("Sicil No*:", self.personnel_number_edit)
        form_layout.addRow("Ad:", self.first_name_edit)
        form_layout.addRow("Soyad:", self.last_name_edit)
        form_layout.addRow("Açık Adres*:", self.address_edit)
        form_layout.addRow("Mahalle*:", self.neighborhood_edit)
        form_layout.addRow("Sokak:", self.street_edit)
        form_layout.addRow("Cadde:", self.avenue_edit)
        form_layout.addRow("İlçe*:", self.district_edit)
        form_layout.addRow("İl*:", self.city_edit)
        form_layout.addRow("Vardiya*:", self.shift_combo)
        form_layout.addRow("Yaka*:", self.collar_combo)
        form_layout.addRow("Aktiflik:", self.active_check)
        form_layout.addRow("Statü*:", self.personnel_type_combo)
        form_layout.addRow("Enlem:", self.latitude_edit)
        form_layout.addRow("Boylam:", self.longitude_edit)
        form_layout.addRow("Staj Başlangıcı:", self.internship_start_edit)
        form_layout.addRow("Staj Bitişi:", self.internship_end_edit)
        self.internship_start_label = form_layout.labelForField(
            self.internship_start_edit
        )
        self.internship_end_label = form_layout.labelForField(
            self.internship_end_edit
        )
        form_root.addLayout(form_layout)

        primary_button_layout = QHBoxLayout()
        secondary_button_layout = QHBoxLayout()
        self.new_button = QPushButton("Yeni Kayıt")
        self.save_button = QPushButton("Kaydet")
        self.update_button = QPushButton("Güncelle")
        self.clear_button = QPushButton("Temizle")
        self.toggle_active_button = QPushButton("Aktif/Pasif")
        for button in (self.new_button, self.save_button, self.update_button):
            primary_button_layout.addWidget(button)
        for button in (self.clear_button, self.toggle_active_button):
            secondary_button_layout.addWidget(button)
        form_root.addLayout(primary_button_layout)
        form_root.addLayout(secondary_button_layout)
        form_root.addStretch()
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        form_scroll.setWidget(form_group)
        splitter.addWidget(form_scroll)
        splitter.setSizes([640, 460])
        root.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.search_button.clicked.connect(self._search)
        self.search_edit.returnPressed.connect(self._search)
        self.clear_search_button.clicked.connect(self._clear_search)
        self.refresh_button.clicked.connect(self.refresh_list)
        self.personnel_table.itemSelectionChanged.connect(self._load_selected_row)
        self.new_button.clicked.connect(self._start_new_record)
        self.save_button.clicked.connect(self._save_new_record)
        self.update_button.clicked.connect(self._update_record)
        self.clear_button.clicked.connect(self._clear_form)
        self.toggle_active_button.clicked.connect(self._toggle_active)
        self.personnel_type_combo.currentIndexChanged.connect(
            self._update_internship_visibility
        )

    def refresh_list(self) -> None:
        try:
            records = self.service.list_personnel()
            self._populate_table(records)
        except PersonnelManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Personel listesi arayüzde yenilenemedi.")
            self._show_error("Personel listesi yenilenemedi.")

    def _search(self) -> None:
        search_text = self.search_edit.text().strip()
        try:
            records = self.service.search_by_personnel_number(search_text)
            self._populate_table(records)
            exact_match = next(
                (
                    record
                    for record in records
                    if record.personnel_number == search_text
                ),
                None,
            )
            if exact_match is not None:
                self._select_personnel(exact_match.id)
            elif not records:
                self._clear_form()
                QMessageBox.information(
                    self,
                    "Personel Arama",
                    "Bu sicil numarasıyla eşleşen personel bulunamadı.",
                )
        except PersonnelManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Personel araması arayüzde tamamlanamadı.")
            self._show_error("Personel araması tamamlanamadı.")

    def _clear_search(self) -> None:
        self.search_edit.clear()
        self.refresh_list()

    def _populate_table(self, records: tuple[PersonnelView, ...]) -> None:
        self.personnel_table.blockSignals(True)
        self.personnel_table.clearContents()
        self.personnel_table.setRowCount(len(records))
        for row_index, personnel in enumerate(records):
            values = (
                personnel.personnel_number,
                personnel.first_name or "",
                personnel.last_name or "",
                personnel.shift,
                personnel.collar,
                "Aktif" if personnel.active else "Pasif",
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, personnel.id)
                self.personnel_table.setItem(row_index, column_index, item)
        self.personnel_table.resizeColumnsToContents()
        self.personnel_table.blockSignals(False)
        self.list_count_label.setText(f"Toplam: {len(records)}")

    def _load_selected_row(self) -> None:
        selected_rows = self.personnel_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        item = self.personnel_table.item(selected_rows[0].row(), 0)
        if item is None:
            return
        personnel_id = item.data(Qt.ItemDataRole.UserRole)
        if personnel_id is None:
            return
        try:
            self._load_personnel(self.service.get_personnel(int(personnel_id)))
        except PersonnelManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Seçilen personel bilgileri forma yüklenemedi.")
            self._show_error("Personel bilgileri forma yüklenemedi.")

    def _load_personnel(self, personnel: PersonnelView) -> None:
        self._selected_personnel_id = personnel.id
        self._selected_active = personnel.active
        self._new_record_mode = False
        self.personnel_number_edit.setText(personnel.personnel_number)
        self.personnel_number_edit.setReadOnly(True)
        self.first_name_edit.setText(personnel.first_name or "")
        self.last_name_edit.setText(personnel.last_name or "")
        self.address_edit.setPlainText(personnel.address)
        self.city_edit.setText(personnel.city or "")
        self.district_edit.setText(personnel.district or "")
        self.neighborhood_edit.setText(personnel.neighborhood or "")
        self.street_edit.setText(personnel.street or "")
        self.avenue_edit.setText(personnel.avenue or "")
        self._set_combo_value(self.shift_combo, personnel.shift)
        self._set_combo_value(self.collar_combo, personnel.collar)
        self.active_check.setChecked(personnel.active)
        self._set_combo_value(self.personnel_type_combo, personnel.personnel_type)
        self.latitude_edit.setText(
            "" if personnel.latitude is None else str(personnel.latitude)
        )
        self.longitude_edit.setText(
            "" if personnel.longitude is None else str(personnel.longitude)
        )
        self.internship_start_edit.setText(
            "" if personnel.internship_start is None else personnel.internship_start.isoformat()
        )
        self.internship_end_edit.setText(
            "" if personnel.internship_end is None else personnel.internship_end.isoformat()
        )
        self._update_internship_visibility()
        self._update_button_states()

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _select_personnel(self, personnel_id: int) -> None:
        for row_index in range(self.personnel_table.rowCount()):
            item = self.personnel_table.item(row_index, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == personnel_id:
                self.personnel_table.selectRow(row_index)
                self.personnel_table.scrollToItem(item)
                return

    def _start_new_record(self) -> None:
        self.personnel_table.clearSelection()
        self._clear_form()
        self._new_record_mode = True
        self.personnel_number_edit.setReadOnly(False)
        self.active_check.setChecked(True)
        self._set_combo_value(
            self.personnel_type_combo,
            PersonelTuru.KADROLU.value,
        )
        self.latitude_edit.clear()
        self.longitude_edit.clear()
        self.internship_start_edit.clear()
        self.internship_end_edit.clear()
        self._update_internship_visibility()
        self._update_button_states()
        self.personnel_number_edit.setFocus()

    def _clear_form(self) -> None:
        self._selected_personnel_id = None
        self._selected_active = None
        self._new_record_mode = False
        self.personnel_number_edit.clear()
        self.personnel_number_edit.setReadOnly(False)
        self.first_name_edit.clear()
        self.last_name_edit.clear()
        self.address_edit.clear()
        self.city_edit.clear()
        self.district_edit.clear()
        self.neighborhood_edit.clear()
        self.street_edit.clear()
        self.avenue_edit.clear()
        self.shift_combo.setCurrentIndex(0)
        self.collar_combo.setCurrentIndex(0)
        self.active_check.setChecked(True)
        self._set_combo_value(
            self.personnel_type_combo,
            PersonelTuru.KADROLU.value,
        )
        self.internship_start_edit.clear()
        self.internship_end_edit.clear()
        self._update_internship_visibility()
        self._update_button_states()

    def _update_internship_visibility(self) -> None:
        is_intern = (
            self.personnel_type_combo.currentData()
            == PersonelTuru.STAJYER.value
        )
        self.internship_start_label.setVisible(is_intern)
        self.internship_start_edit.setVisible(is_intern)
        self.internship_end_label.setVisible(is_intern)
        self.internship_end_edit.setVisible(is_intern)

    def _update_button_states(self) -> None:
        selected = self._selected_personnel_id is not None
        self.save_button.setEnabled(self._new_record_mode and not selected)
        self.update_button.setEnabled(selected)
        self.toggle_active_button.setEnabled(selected)
        self.toggle_active_button.setText(
            "Pasif Yap" if selected and self._selected_active else "Aktif Yap"
        )

    def _form_data(self) -> PersonnelFormData:
        return PersonnelFormData(
            personnel_number=self.personnel_number_edit.text(),
            first_name=self.first_name_edit.text(),
            last_name=self.last_name_edit.text(),
            address=self.address_edit.toPlainText(),
            city=self.city_edit.text(),
            district=self.district_edit.text(),
            neighborhood=self.neighborhood_edit.text(),
            street=self.street_edit.text(),
            avenue=self.avenue_edit.text(),
            shift=str(self.shift_combo.currentData() or ""),
            collar=str(self.collar_combo.currentData() or ""),
            personnel_type=str(self.personnel_type_combo.currentData() or ""),
            internship_start=(
                self.internship_start_edit.text()
                if self.personnel_type_combo.currentData()
                == PersonelTuru.STAJYER.value
                else None
            ),
            internship_end=(
                self.internship_end_edit.text()
                if self.personnel_type_combo.currentData()
                == PersonelTuru.STAJYER.value
                else None
            ),
            active=self.active_check.isChecked(),
        )

    def _save_new_record(self) -> None:
        if not self._new_record_mode:
            return
        form_data = self._form_data()
        result = self._save_with_warning_confirmation(
            lambda allow: self.service.create_personnel(
                form_data,
                allow_warnings=allow,
            )
        )
        if result is None:
            return
        QMessageBox.information(self, "Personel Kaydı", "Personel başarıyla kaydedildi.")
        self.refresh_list()
        self._select_personnel(result.personnel.id)

    def _update_record(self) -> None:
        if self._selected_personnel_id is None:
            return
        personnel_id = self._selected_personnel_id
        form_data = self._form_data()
        result = self._save_with_warning_confirmation(
            lambda allow: self.service.update_personnel(
                personnel_id,
                form_data,
                allow_warnings=allow,
            )
        )
        if result is None:
            return
        QMessageBox.information(
            self,
            "Personel Güncelleme",
            "Personel bilgileri başarıyla güncellendi.",
        )
        if result.assignment_review_recommended:
            QMessageBox.warning(
                self,
                "Servis Ataması Uyarısı",
                self.REASSESSMENT_MESSAGE,
            )
        self.refresh_list()
        self._select_personnel(result.personnel.id)

    def _save_with_warning_confirmation(self, operation):
        try:
            return operation(False)
        except PersonnelWarningsRequireConfirmation as warning:
            answer = QMessageBox.question(
                self,
                "Personel Bilgisi Uyarısı",
                "\n".join(warning.messages)
                + "\n\nBu uyarılara rağmen kaydetmek istiyor musunuz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            try:
                return operation(True)
            except PersonnelManagementError as error:
                self._show_error(str(error))
                return None
        except PersonnelManagementError as error:
            self._show_error(str(error))
            return None
        except Exception:
            logger.exception("Personel form işlemi beklenmeyen hatayla sonuçlandı.")
            self._show_error("Personel işlemi tamamlanamadı.")
            return None

    def _toggle_active(self) -> None:
        if self._selected_personnel_id is None:
            return
        target_active = not bool(self._selected_active)
        try:
            personnel = self.service.set_personnel_active(
                self._selected_personnel_id,
                target_active,
            )
            QMessageBox.information(
                self,
                "Personel Aktiflik Durumu",
                "Personel aktif duruma alındı."
                if target_active
                else "Personel pasif duruma alındı.",
            )
            self.refresh_list()
            self._select_personnel(personnel.id)
        except PersonnelManagementError as error:
            self._show_error(str(error))
        except Exception:
            logger.exception("Personel aktiflik işlemi arayüzde tamamlanamadı.")
            self._show_error("Personelin aktiflik durumu değiştirilemedi.")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Personel İşlemi Hatası", message)
