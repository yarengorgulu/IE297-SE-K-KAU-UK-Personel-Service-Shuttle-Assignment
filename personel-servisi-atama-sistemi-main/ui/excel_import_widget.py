"""Excel seçme, analiz etme ve doğrulama önizleme arayüzü."""

from __future__ import annotations

from datetime import date, datetime
from functools import partial
from typing import Any

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from excel_import import (
    AnalysisReport,
    PreviewRow,
    PreviewStatus,
    SheetInfo,
    WorkbookInfo,
    analyze_workbook,
    detect_record_type,
    inspect_workbook,
)
from normalization.columns import COLUMN_ALIASES
from normalization.result import RecordType
from services import (
    ConflictBehavior,
    PersonnelImportPlan,
    PersonnelImportResult,
    PersonnelImportService,
    ServiceCapacityImportPlan,
    ServiceCapacityImportResult,
    ServiceCapacityImportService,
    ServiceRouteImportPlan,
    ServiceRouteImportResult,
    ServiceRouteImportService,
)
from ui.column_mapping_dialog import ColumnMappingDialog
from ui.personnel_import_dialog import PersonnelImportDialog
from ui.service_capacity_import_dialog import ServiceCapacityImportDialog
from ui.service_route_import_dialog import ServiceRouteImportDialog
from ui.workers import FunctionWorker


TYPE_LABELS = {
    RecordType.PERSONEL: "Personel",
    RecordType.SERVIS_KAPASITE: "Servis Kapasite",
    RecordType.SERVIS_GUZERGAH: "Servis Mahalle/Güzergâh",
}
PREVIEW_ROW_LIMIT = 1000


class ExcelImportWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[FunctionWorker] = set()
        self._workbook_info: WorkbookInfo | None = None
        self._report: AnalysisReport | None = None
        self._mapping_overrides: dict[int, str | None] = {}
        self._import_in_progress = False
        self._report_imported = False
        self._build_ui()
        self._connect_signals()
        self._reset_results()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        source_group = QGroupBox("Excel Dosyası")
        source_layout = QGridLayout(source_group)
        self.choose_file_button = QPushButton("Dosya Seç")
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)
        self.file_path_edit.setPlaceholderText("Henüz bir Excel dosyası seçilmedi")
        source_layout.addWidget(self.choose_file_button, 0, 0)
        source_layout.addWidget(self.file_path_edit, 0, 1, 1, 3)

        self.sheet_combo = QComboBox()
        self.file_type_combo = QComboBox()
        self.file_type_combo.addItem("Otomatik belirle", None)
        for record_type, label in TYPE_LABELS.items():
            self.file_type_combo.addItem(label, record_type.value)
        source_layout.addWidget(QLabel("Çalışma sayfası:"), 1, 0)
        source_layout.addWidget(self.sheet_combo, 1, 1)
        source_layout.addWidget(QLabel("Dosya türü:"), 1, 2)
        source_layout.addWidget(self.file_type_combo, 1, 3)

        button_layout = QHBoxLayout()
        self.analyze_button = QPushButton("Dosyayı Analiz Et")
        self.mapping_button = QPushButton("Sütun Eşleştirmeyi Düzenle")
        self.import_capacity_button = QPushButton(
            "Geçerli Servis Kayıtlarını Aktar"
        )
        self.import_personnel_button = QPushButton(
            "Geçerli Personel Kayıtlarını Aktar"
        )
        self.import_route_button = QPushButton(
            "Geçerli Güzergâh Kayıtlarını Aktar"
        )
        self.clear_button = QPushButton("Temizle")
        button_layout.addWidget(self.analyze_button)
        button_layout.addWidget(self.mapping_button)
        button_layout.addStretch()
        button_layout.addWidget(self.clear_button)
        source_layout.addLayout(button_layout, 2, 0, 1, 4)

        import_button_layout = QHBoxLayout()
        import_button_layout.addWidget(self.import_capacity_button)
        import_button_layout.addWidget(self.import_personnel_button)
        import_button_layout.addWidget(self.import_route_button)
        import_button_layout.addStretch()
        source_layout.addLayout(import_button_layout, 3, 0, 1, 4)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        source_layout.addWidget(self.progress_bar, 4, 0, 1, 4)
        root.addWidget(source_group)

        counts_layout = QHBoxLayout()
        self.total_label = QLabel()
        self.valid_label = QLabel()
        self.warning_label = QLabel()
        self.error_label = QLabel()
        for label in (
            self.total_label,
            self.valid_label,
            self.warning_label,
            self.error_label,
        ):
            label.setStyleSheet("font-weight: 600; padding: 6px 12px;")
            counts_layout.addWidget(label)
        counts_layout.addStretch()
        counts_layout.addWidget(QLabel("Filtre:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("Tümü", None)
        self.filter_combo.addItem("Geçerli", PreviewStatus.GECERLI.value)
        self.filter_combo.addItem("Uyarılı", PreviewStatus.UYARILI.value)
        self.filter_combo.addItem("Hatalı", PreviewStatus.HATALI.value)
        counts_layout.addWidget(self.filter_combo)
        root.addLayout(counts_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_table = QTableWidget()
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.preview_table.setAlternatingRowColors(True)
        splitter.addWidget(self.preview_table)

        details_widget = QWidget()
        details_layout = QHBoxLayout(details_widget)
        mapping_box = QGroupBox("Sütun Eşleştirme Özeti")
        mapping_layout = QVBoxLayout(mapping_box)
        self.mapping_summary = QTextEdit()
        self.mapping_summary.setReadOnly(True)
        mapping_layout.addWidget(self.mapping_summary)
        details_layout.addWidget(mapping_box)

        validation_box = QGroupBox("Hata ve Uyarı Detayı")
        validation_layout = QVBoxLayout(validation_box)
        self.validation_detail = QTextEdit()
        self.validation_detail.setReadOnly(True)
        validation_layout.addWidget(self.validation_detail)
        details_layout.addWidget(validation_box)
        splitter.addWidget(details_widget)
        splitter.setSizes([430, 180])
        root.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.choose_file_button.clicked.connect(self._choose_file)
        self.analyze_button.clicked.connect(self._analyze)
        self.mapping_button.clicked.connect(self._edit_mapping)
        self.import_capacity_button.clicked.connect(self._prepare_capacity_import)
        self.import_personnel_button.clicked.connect(self._prepare_personnel_import)
        self.import_route_button.clicked.connect(self._prepare_route_import)
        self.clear_button.clicked.connect(self.clear)
        self.sheet_combo.currentIndexChanged.connect(self._sheet_changed)
        self.file_type_combo.currentIndexChanged.connect(self._file_type_changed)
        self.filter_combo.currentIndexChanged.connect(self._populate_preview)
        self.preview_table.itemSelectionChanged.connect(self._show_selected_detail)

    def _set_busy(self, busy: bool) -> None:
        self.progress_bar.setVisible(busy)
        self.choose_file_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy and self._selected_sheet() is not None)
        self.mapping_button.setEnabled(
            not busy and self._selected_sheet() is not None
        )
        self.import_capacity_button.setEnabled(
            not busy and self._capacity_import_available()
        )
        self.import_personnel_button.setEnabled(
            not busy and self._personnel_import_available()
        )
        self.import_route_button.setEnabled(
            not busy and self._route_import_available()
        )

    def _run_async(self, function, on_result, on_error=None) -> None:
        worker = FunctionWorker(function)
        self._workers.add(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error or self._show_error)
        worker.signals.finished.connect(partial(self._worker_finished, worker))
        self._set_busy(True)
        self.thread_pool.start(worker)

    def _worker_finished(self, worker: FunctionWorker) -> None:
        self._workers.discard(worker)
        self._set_busy(bool(self._workers))

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Excel Okuma Hatası", message)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Excel Dosyası Seç",
            "",
            "Excel Dosyaları (*.xlsx *.xlsm)",
        )
        if not path:
            return
        self.clear(keep_path=True)
        self.file_path_edit.setText(path)
        self._run_async(lambda: inspect_workbook(path), self._workbook_loaded)

    def _workbook_loaded(self, workbook_info: WorkbookInfo) -> None:
        self._workbook_info = workbook_info
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        first_non_empty = -1
        for sheet in workbook_info.sheets:
            label = sheet.name if not sheet.is_empty else f"{sheet.name} (boş)"
            self.sheet_combo.addItem(label, sheet)
            item = self.sheet_combo.model().item(self.sheet_combo.count() - 1)
            if sheet.is_empty:
                item.setEnabled(False)
            elif first_non_empty == -1:
                first_non_empty = self.sheet_combo.count() - 1
        self.sheet_combo.blockSignals(False)
        if first_non_empty >= 0:
            self.sheet_combo.setCurrentIndex(first_non_empty)
            self._sheet_changed()
        else:
            self.mapping_summary.setPlainText("Dosyadaki tüm çalışma sayfaları boş.")
        self._set_busy(False)

    def _selected_sheet(self) -> SheetInfo | None:
        sheet = self.sheet_combo.currentData()
        return sheet if isinstance(sheet, SheetInfo) and not sheet.is_empty else None

    def _sheet_changed(self) -> None:
        self._mapping_overrides.clear()
        self._report = None
        self._reset_results()
        sheet = self._selected_sheet()
        if sheet is None:
            return
        detection = detect_record_type(sheet.headers)
        if detection.is_confident and detection.recommended_type is not None:
            index = self.file_type_combo.findData(detection.recommended_type.value)
            self.file_type_combo.setCurrentIndex(index)
        else:
            self.file_type_combo.setCurrentIndex(0)
        self.mapping_summary.setPlainText(self._detection_text(detection))
        self.analyze_button.setEnabled(True)
        self.mapping_button.setEnabled(True)

    def _selected_record_type(self) -> RecordType | None:
        value = self.file_type_combo.currentData()
        if value is None:
            return None
        try:
            return RecordType(value)
        except ValueError:
            return None

    def _file_type_changed(self) -> None:
        self._mapping_overrides.clear()
        self._report = None
        self._report_imported = False
        self._reset_results()
        available = self._selected_sheet() is not None
        self.analyze_button.setEnabled(available)
        self.mapping_button.setEnabled(available)

    def _analyze(self) -> None:
        sheet = self._selected_sheet()
        if sheet is None:
            QMessageBox.warning(self, "Çalışma Sayfası", "Lütfen dolu bir sayfa seçin.")
            return
        record_type = self._selected_record_type()
        if record_type is None:
            detection = detect_record_type(sheet.headers)
            if not detection.is_confident or detection.recommended_type is None:
                QMessageBox.warning(
                    self,
                    "Dosya Türü Seçimi",
                    "Dosya türü kesin olarak belirlenemedi. Lütfen dosya türünü seçin.",
                )
                return
            record_type = detection.recommended_type
        path = self.file_path_edit.text()
        overrides = dict(self._mapping_overrides)
        self._run_async(
            lambda: analyze_workbook(path, sheet.name, record_type, overrides),
            self._analysis_ready,
        )

    def _analysis_ready(self, report: AnalysisReport) -> None:
        self._report = report
        self._report_imported = False
        self.total_label.setText(f"Toplam: {report.total_count}")
        self.valid_label.setText(f"Geçerli: {report.valid_count}")
        self.warning_label.setText(f"Uyarılı: {report.warning_count}")
        self.error_label.setText(f"Hatalı: {report.error_count}")
        self.mapping_summary.setPlainText(self._mapping_text(report))
        self._populate_preview()
        self.import_capacity_button.setEnabled(self._capacity_import_available())
        self.import_personnel_button.setEnabled(self._personnel_import_available())
        self.import_route_button.setEnabled(self._route_import_available())

    def _personnel_import_available(self) -> bool:
        return bool(
            self._report is not None
            and self._report.record_type is RecordType.PERSONEL
            and any(row.validation.is_valid for row in self._report.rows)
            and not self._import_in_progress
            and not self._report_imported
        )

    def _capacity_import_available(self) -> bool:
        return bool(
            self._report is not None
            and self._report.record_type is RecordType.SERVIS_KAPASITE
            and any(row.validation.is_valid for row in self._report.rows)
            and not self._import_in_progress
            and not self._report_imported
        )

    def _route_import_available(self) -> bool:
        return bool(
            self._report is not None
            and self._report.record_type is RecordType.SERVIS_GUZERGAH
            and any(row.validation.is_valid for row in self._report.rows)
            and not self._import_in_progress
            and not self._report_imported
        )

    def _prepare_capacity_import(self) -> None:
        if self._import_in_progress or not self._capacity_import_available():
            return
        report = self._report
        if report is None:
            return
        self._import_in_progress = True
        self._run_async(
            lambda: ServiceCapacityImportService().preview_import(
                report,
                ConflictBehavior.SKIP,
                include_warning_rows=True,
            ),
            self._show_capacity_import_confirmation,
            self._show_capacity_import_error,
        )

    def _show_capacity_import_confirmation(
        self,
        plan: ServiceCapacityImportPlan,
    ) -> None:
        dialog = ServiceCapacityImportDialog(plan, self)
        if not dialog.exec():
            self._import_in_progress = False
            self._set_busy(bool(self._workers))
            return
        report = self._report
        if report is None:
            self._import_in_progress = False
            return
        behavior = dialog.conflict_behavior()
        self._run_async(
            lambda: ServiceCapacityImportService().import_report(
                report,
                behavior,
                include_warning_rows=True,
            ),
            self._capacity_import_completed,
            self._show_capacity_import_error,
        )

    def _capacity_import_completed(
        self,
        result: ServiceCapacityImportResult,
    ) -> None:
        self._import_in_progress = False
        self._report_imported = True
        self.import_capacity_button.setEnabled(False)
        QMessageBox.information(
            self,
            "Servis Kapasite Aktarımı",
            "Servis kapasite aktarımı tamamlandı.\n\n"
            f"Eklenen fiziksel servis: {result.added_service_count}\n"
            f"Eklenen servis vardiyası: {result.added_shift_count}\n"
            f"Güncellenen kayıt: {result.updated_count}\n"
            f"Atlanan kayıt: {result.skipped_count}\n"
            f"Hatalı kayıt: {result.invalid_count}\n"
            f"Onayla aktarılan uyarılı kayıt: {result.imported_warning_count}",
        )

    def _show_capacity_import_error(self, message: str) -> None:
        self._import_in_progress = False
        self._set_busy(bool(self._workers))
        QMessageBox.critical(self, "Servis Kapasite Aktarım Hatası", message)

    def _prepare_personnel_import(self) -> None:
        if self._import_in_progress or not self._personnel_import_available():
            return
        report = self._report
        if report is None:
            return
        self._import_in_progress = True
        self._run_async(
            lambda: PersonnelImportService().preview_import(
                report,
                ConflictBehavior.SKIP,
                include_warning_rows=True,
            ),
            self._show_personnel_import_confirmation,
            self._show_personnel_import_error,
        )

    def _show_personnel_import_confirmation(
        self,
        plan: PersonnelImportPlan,
    ) -> None:
        dialog = PersonnelImportDialog(plan, self)
        if not dialog.exec():
            self._import_in_progress = False
            self._set_busy(bool(self._workers))
            return
        report = self._report
        if report is None:
            self._import_in_progress = False
            return
        behavior = dialog.conflict_behavior()
        self._run_async(
            lambda: PersonnelImportService().import_report(
                report,
                behavior,
                include_warning_rows=True,
            ),
            self._personnel_import_completed,
            self._show_personnel_import_error,
        )

    def _personnel_import_completed(self, result: PersonnelImportResult) -> None:
        self._import_in_progress = False
        self._report_imported = True
        self.import_personnel_button.setEnabled(False)
        QMessageBox.information(
            self,
            "Personel Aktarımı",
            "Personel aktarımı tamamlandı.\n\n"
            f"Eklenen personel: {result.added_count}\n"
            f"Güncellenen personel: {result.updated_count}\n"
            f"Atlanan kayıt: {result.skipped_count}\n"
            f"Hatalı kayıt: {result.invalid_count}\n"
            f"Onayla aktarılan uyarılı kayıt: {result.imported_warning_count}",
        )

    def _show_personnel_import_error(self, message: str) -> None:
        self._import_in_progress = False
        self._set_busy(bool(self._workers))
        QMessageBox.critical(self, "Personel Aktarım Hatası", message)

    def _prepare_route_import(self) -> None:
        if self._import_in_progress or not self._route_import_available():
            return
        report = self._report
        if report is None:
            return
        self._import_in_progress = True
        self._run_async(
            lambda: ServiceRouteImportService().preview_import(report),
            self._show_route_import_confirmation,
            self._show_route_import_error,
        )

    def _show_route_import_confirmation(
        self,
        plan: ServiceRouteImportPlan,
    ) -> None:
        dialog = ServiceRouteImportDialog(plan, self)
        if not dialog.exec():
            self._import_in_progress = False
            self._set_busy(bool(self._workers))
            return
        report = self._report
        if report is None:
            self._import_in_progress = False
            return
        self._run_async(
            lambda: ServiceRouteImportService().import_report(report),
            self._route_import_completed,
            self._show_route_import_error,
        )

    def _route_import_completed(self, result: ServiceRouteImportResult) -> None:
        self._import_in_progress = False
        self._report_imported = True
        self.import_route_button.setEnabled(False)
        QMessageBox.information(
            self,
            "Güzergâh Aktarımı",
              "Güzergâh aktarımı tamamlandı.\n\n"
              f"Eklenen nokta: {result.added_count}\n"
              f"Atlanan eşleşme: {result.skipped_count}\n"
              f"Hatalı eşleşme: {result.invalid_count}\n"
              f"Mükerrer nokta: {result.duplicate_count}\n"
              f"Bulunmayan servis: {result.missing_service_count}",
          )

    def _show_route_import_error(self, message: str) -> None:
        self._import_in_progress = False
        self._set_busy(bool(self._workers))
        QMessageBox.critical(self, "Güzergâh Aktarım Hatası", message)

    def _detection_text(self, detection) -> str:
        lines = ["Otomatik dosya türü puanları:"]
        for record_type in RecordType:
            lines.append(
                f"- {TYPE_LABELS[record_type]}: %{detection.scores[record_type] * 100:.0f}"
            )
        if detection.is_confident and detection.recommended_type is not None:
            lines.append(f"Önerilen tür: {TYPE_LABELS[detection.recommended_type]}")
        else:
            lines.append("Tahmin kesin değil; dosya türünü kullanıcı seçmelidir.")
        return "\n".join(lines)

    def _mapping_text(self, report: AnalysisReport) -> str:
        lines = [self._detection_text(report.detection), "", "Sütun eşleştirmeleri:"]
        for entry in report.mapping.entries:
            target = entry.target_field or "Fazladan sütun olarak korundu"
            manual = " (manuel)" if entry.is_manual else ""
            lines.append(f"- {entry.source_header} → {target}{manual}")
        if report.mapping.missing_required_columns:
            lines.extend(
                [
                    "",
                    "Eksik zorunlu sütunlar: "
                    + ", ".join(report.mapping.missing_required_columns),
                ]
            )
        if report.mapping.duplicate_targets:
            lines.append("")
            for target, sources in report.mapping.duplicate_targets.items():
                lines.append(f"Çakışan hedef {target}: {', '.join(sources)}")
        if report.skipped_blank_rows:
            lines.append(f"\nAtlanan tamamen boş satır: {report.skipped_blank_rows}")
        return "\n".join(lines)

    def _populate_preview(self) -> None:
        report = self._report
        self.preview_table.clear()
        if report is None:
            self.preview_table.setRowCount(0)
            self.preview_table.setColumnCount(0)
            return

        selected_value = self.filter_combo.currentData()
        selected_status = (
            PreviewStatus(selected_value) if selected_value is not None else None
        )
        filtered_rows = [
            row
            for row in report.rows
            if selected_status is None or row.status is selected_status
        ]
        rows = filtered_rows[:PREVIEW_ROW_LIMIT]
        if len(filtered_rows) > PREVIEW_ROW_LIMIT:
            self.preview_table.setToolTip(
                f"Performans için {len(filtered_rows)} kaydın ilk "
                f"{PREVIEW_ROW_LIMIT} satırı gösteriliyor."
            )
        else:
            self.preview_table.setToolTip("")
        canonical_fields = list(COLUMN_ALIASES[report.record_type])
        extra_fields: list[str] = []
        for row in report.rows:
            for field in row.extra_data:
                if field not in extra_fields:
                    extra_fields.append(field)
        headers = [
            "DURUM",
            "EXCEL SATIRI",
            *canonical_fields,
            *extra_fields,
            "HATALAR",
            "UYARILAR",
        ]
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setRowCount(len(rows))
        self.preview_table.setUpdatesEnabled(False)

        colors = {
            PreviewStatus.GECERLI: QColor("#DCFCE7"),
            PreviewStatus.UYARILI: QColor("#FEF3C7"),
            PreviewStatus.HATALI: QColor("#FEE2E2"),
        }
        for row_index, preview_row in enumerate(rows):
            values = [
                preview_row.status.value,
                preview_row.row_number,
                *[
                    preview_row.validation.normalized_data.get(field)
                    for field in canonical_fields
                ],
                *[preview_row.extra_data.get(field) for field in extra_fields],
                preview_row.error_text,
                preview_row.warning_text,
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(self._format_value(value))
                item.setBackground(colors[preview_row.status])
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, preview_row)
                self.preview_table.setItem(row_index, column_index, item)
        self.preview_table.resizeColumnsToContents()
        self.preview_table.setUpdatesEnabled(True)

    @staticmethod
    def _format_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

    def _show_selected_detail(self) -> None:
        selected = self.preview_table.selectedItems()
        if not selected:
            self.validation_detail.clear()
            return
        first_item = self.preview_table.item(selected[0].row(), 0)
        row = first_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, PreviewRow):
            return
        text = [f"Excel satırı: {row.row_number}", f"Durum: {row.status.value}"]
        if row.validation.errors:
            text.append("\nHatalar:")
            text.extend(f"- {message.message}" for message in row.validation.errors)
        if row.validation.warnings:
            text.append("\nUyarılar:")
            text.extend(f"- {message.message}" for message in row.validation.warnings)
        self.validation_detail.setPlainText("\n".join(text))

    def _edit_mapping(self) -> None:
        sheet = self._selected_sheet()
        if sheet is None:
            return
        record_type = self._selected_record_type()
        if record_type is None:
            QMessageBox.warning(
                self,
                "Dosya Türü Seçimi",
                "Manuel sütun eşleştirmeden önce dosya türünü seçin.",
            )
            return
        dialog = ColumnMappingDialog(
            sheet.headers,
            record_type,
            self._mapping_overrides,
            self,
        )
        if dialog.exec():
            self._mapping_overrides = dialog.mapping_overrides()
            self._analyze()

    def _reset_results(self) -> None:
        self.total_label.setText("Toplam: 0")
        self.valid_label.setText("Geçerli: 0")
        self.warning_label.setText("Uyarılı: 0")
        self.error_label.setText("Hatalı: 0")
        self.validation_detail.clear()
        self.preview_table.clear()
        self.preview_table.setRowCount(0)
        self.preview_table.setColumnCount(0)
        self.analyze_button.setEnabled(False)
        self.mapping_button.setEnabled(False)
        self.import_capacity_button.setEnabled(False)
        self.import_personnel_button.setEnabled(False)
        self.import_route_button.setEnabled(False)

    def clear(self, *, keep_path: bool = False) -> None:
        self._workbook_info = None
        self._report = None
        self._report_imported = False
        self._import_in_progress = False
        self._mapping_overrides.clear()
        self.sheet_combo.clear()
        self.file_type_combo.setCurrentIndex(0)
        self.filter_combo.setCurrentIndex(0)
        self.mapping_summary.clear()
        if not keep_path:
            self.file_path_edit.clear()
        self._reset_results()
