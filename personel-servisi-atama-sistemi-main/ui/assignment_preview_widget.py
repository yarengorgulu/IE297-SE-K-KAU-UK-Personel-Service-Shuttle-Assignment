"""Aşama 12 Personel Atama uygunluk ve önizleme ekranı."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.assignment_preview import (
    AssignmentPreview,
    AssignmentPreviewError,
    AssignmentPreviewResult,
    AssignmentPreviewService,
    PersonnelOption,
)
from services.assignment_preview_rendering import render_assignment_preview_html
from ui.workers import FunctionWorker


class AssignmentPreviewWidget(QWidget):
    """Önizleme ve açık kullanıcı onayıyla personel servis ataması ekranı."""

    assignments_changed = Signal()

    RESULT_HEADERS = (
        "Sicil",
        "Personel",
        "Tür",
        "Vardiya",
        "Yaka",
        "Servis",
        "Rota",
        "Kapasite",
        "Doluluk (önce/sonra)",
        "Kalan",
        "Uzaklık",
        "Önerilen Biniş",
        "Durum",
        "Açıklama",
        "Alternatif",
    )

    def __init__(
        self,
        parent=None,
        *,
        service: AssignmentPreviewService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or AssignmentPreviewService()
        self._preview: AssignmentPreview | None = None
        self._visible_results: list[AssignmentPreviewResult] = []
        self._manual_choices: dict[int, int] = {}
        self._personnel_options: dict[int, PersonnelOption] = {}
        self._busy = False
        self._active_worker: FunctionWorker | None = None
        self._build_ui()
        self._connect_signals()
        self._load_personnel()
        self._show_map(None)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)

        controls = QGroupBox("Değerlendirme Yöntemi")
        controls_layout = QHBoxLayout(controls)
        self.personnel_number_edit = QLineEdit()
        self.personnel_number_edit.setPlaceholderText("Sicil numarası")
        self.personnel_number_edit.setMaximumWidth(170)
        self.single_button = QPushButton("Sicile Göre Değerlendir")
        self.selected_button = QPushButton("Seçili Personelleri Değerlendir")
        self.all_button = QPushButton("Tüm Uygun Atanmamışları Değerlendir")
        self.distance_spin = QSpinBox()
        self.distance_spin.setRange(1, 100_000)
        self.distance_spin.setSuffix(" m")
        self.distance_spin.setValue(round(self.service.get_max_distance_meters()))
        self.distance_spin.setToolTip("Personelin kayıtlı rotaya kabul edilen azami uzaklığı")
        self.save_distance_button = QPushButton("Sınırı Varsayılan Yap")
        self.cancel_button = QPushButton("Önizlemeyi İptal Et")
        self.cancel_button.setEnabled(False)
        controls_layout.addWidget(QLabel("Sicil:"))
        controls_layout.addWidget(self.personnel_number_edit)
        controls_layout.addWidget(self.single_button)
        controls_layout.addWidget(self.selected_button)
        controls_layout.addWidget(self.all_button)
        controls_layout.addStretch()
        controls_layout.addWidget(QLabel("Yakınlık sınırı:"))
        controls_layout.addWidget(self.distance_spin)
        controls_layout.addWidget(self.save_distance_button)
        controls_layout.addWidget(self.cancel_button)
        root.addWidget(controls)

        self.info_label = QLabel(
            "Değerlendirme yalnızca öneri üretir. Personel ancak aşağıdaki onay düğmelerinden "
            "biri kullanıldığında kalıcı olarak atanır."
        )
        self.info_label.setStyleSheet(
            "background:#eaf4ff;border:1px solid #b8d8f2;padding:7px;color:#254b6e;"
        )
        root.addWidget(self.info_label)

        commit_layout = QHBoxLayout()
        commit_layout.addStretch()
        self.commit_selected_button = QPushButton("Seçili Öneriyi Atamayı Onayla")
        self.commit_all_button = QPushButton("Tüm Uygun Önerileri Atamayı Onayla")
        self.commit_selected_button.setEnabled(False)
        self.commit_all_button.setEnabled(False)
        self.commit_selected_button.setStyleSheet("font-weight:600;padding:6px 12px;")
        self.commit_all_button.setStyleSheet(
            "font-weight:600;padding:6px 12px;background:#1769aa;color:white;"
        )
        commit_layout.addWidget(self.commit_selected_button)
        commit_layout.addWidget(self.commit_all_button)
        root.addLayout(commit_layout)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        left = QWidget()
        left.setMinimumWidth(360)
        left.setMaximumWidth(520)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.side_tabs = QTabWidget()
        self.side_tabs.setMinimumHeight(250)

        personnel_group = QGroupBox("Personel Seçimi")
        personnel_layout = QVBoxLayout(personnel_group)
        personnel_help = QLabel(
            "Tek personel için normal tıklayın. Birden fazla personel için Ctrl/Shift "
            "tuşunu kullanın; tek kişi seçmek tamamen yeterlidir."
        )
        personnel_help.setWordWrap(True)
        self.personnel_list = QListWidget()
        self.personnel_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.personnel_list.setMinimumHeight(180)
        personnel_layout.addWidget(personnel_help)
        personnel_layout.addWidget(self.personnel_list)
        self.personnel_address_label = QLabel("Bir personel seçtiğinizde adresi burada görünür.")
        self.personnel_address_label.setWordWrap(True)
        self.personnel_address_label.setStyleSheet(
            "background:#f7f8fa;border:1px solid #d8dde5;padding:6px;color:#394554;"
        )
        personnel_layout.addWidget(self.personnel_address_label)
        self.side_tabs.addTab(personnel_group, "Personel Seçimi")

        alternative_group = QGroupBox("İsteğe Bağlı Alternatif Uygun Rota")
        alternative_layout = QVBoxLayout(alternative_group)
        alternative_help = QLabel(
            "Ana öneriyi istemezseniz aynı personele uygun başka bir servis rotasını "
            "buradan seçebilirsiniz. Seçim tek başına atama yapmaz."
        )
        alternative_help.setWordWrap(True)
        self.alternative_combo = QComboBox()
        self.apply_alternative_button = QPushButton("Alternatif Rotayı Önizle")
        self.apply_alternative_button.setEnabled(False)
        alternative_layout.addWidget(alternative_help)
        alternative_layout.addWidget(self.alternative_combo)
        alternative_layout.addWidget(self.apply_alternative_button)
        self.side_tabs.addTab(alternative_group, "Alternatif Rota")

        need_group = QGroupBox("Kapasite Yetersizliği (Yalnızca Bilgilendirme)")
        need_layout = QVBoxLayout(need_group)
        need_help = QLabel(
            "Yer kalmayan vardiyaları ve yoğun bölgeleri gösterir. Buradan otomatik yeni "
            "servis oluşturulmaz."
        )
        need_help.setWordWrap(True)
        self.need_table = QTableWidget(0, 6)
        self.need_table.setHorizontalHeaderLabels(
            ("Vardiya", "Yaka", "Kişi", "İhtiyaç", "Yoğun Alan", "Öneri")
        )
        self.need_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.need_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.need_table.horizontalHeader().setStretchLastSection(True)
        self.need_table.setMinimumHeight(150)
        need_layout.addWidget(need_help)
        need_layout.addWidget(self.need_table)
        self.side_tabs.addTab(need_group, "Kapasite İhtiyacı")
        left_layout.addWidget(self.side_tabs, 1)
        main_splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItem("Tüm durumlar", "")
        self.status_filter.addItem("Yalnız uygun", "UYGUN")
        self.status_filter.addItem("Yalnız atanmış", "ATANDI")
        self.status_filter.addItem("Yalnız atanamayan", "ATANAMADI")
        self.shift_filter = QComboBox()
        self.shift_filter.addItem("Tüm vardiyalar", "")
        self.collar_filter = QComboBox()
        self.collar_filter.addItem("Tüm yakalar", "")
        self.summary_label = QLabel("Henüz önizleme yapılmadı.")
        filter_layout.addWidget(QLabel("Filtre:"))
        filter_layout.addWidget(self.status_filter)
        filter_layout.addWidget(self.shift_filter)
        filter_layout.addWidget(self.collar_filter)
        filter_layout.addStretch()
        filter_layout.addWidget(self.summary_label)
        right_layout.addLayout(filter_layout)

        self.result_table = QTableWidget(0, len(self.RESULT_HEADERS))
        self.result_table.setHorizontalHeaderLabels(self.RESULT_HEADERS)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setMinimumHeight(230)
        self.result_table.setMaximumHeight(340)
        right_layout.addWidget(self.result_table, 1)
        main_splitter.addWidget(right)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setMinimumHeight(270)
        main_splitter.setMaximumHeight(390)
        content_splitter = QSplitter(Qt.Orientation.Vertical)
        content_splitter.setChildrenCollapsible(False)
        content_splitter.addWidget(main_splitter)

        map_group = QGroupBox("Personel ve Atandığı Servis Rotası")
        map_group.setMinimumHeight(300)
        map_layout = QVBoxLayout(map_group)
        self.map_hint_label = QLabel(
            "Personel listesinden bir veya daha fazla kişi seçin. Uygun sonuç seçildiğinde "
            "servis rotası ve önerilen biniş noktası da gösterilir."
        )
        self.map_hint_label.setWordWrap(True)
        self.map_view = QWebEngineView()
        self.map_view.setMinimumSize(620, 300)
        map_layout.addWidget(self.map_hint_label)
        map_layout.addWidget(self.map_view, 1)
        content_splitter.addWidget(map_group)
        content_splitter.setStretchFactor(0, 3)
        content_splitter.setStretchFactor(1, 4)
        content_splitter.setSizes([340, 460])
        root.addWidget(content_splitter, 1)

    def _connect_signals(self) -> None:
        self.single_button.clicked.connect(self._preview_single)
        self.selected_button.clicked.connect(self._preview_selected)
        self.all_button.clicked.connect(self._preview_all)
        self.save_distance_button.clicked.connect(self._save_distance)
        self.cancel_button.clicked.connect(self._cancel_preview)
        self.result_table.itemSelectionChanged.connect(self._result_selected)
        self.apply_alternative_button.clicked.connect(self._apply_alternative)
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        self.shift_filter.currentIndexChanged.connect(self._apply_filters)
        self.collar_filter.currentIndexChanged.connect(self._apply_filters)
        self.personnel_number_edit.returnPressed.connect(self._preview_single)
        self.personnel_list.itemSelectionChanged.connect(
            self._personnel_selection_changed
        )
        self.commit_selected_button.clicked.connect(self._commit_selected)
        self.commit_all_button.clicked.connect(self._commit_all)

    def _load_personnel(self) -> None:
        selected_ids = {
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.personnel_list.selectedItems()
        }
        self.personnel_list.clear()
        options = self.service.list_personnel_options()
        self._personnel_options = {option.id: option for option in options}
        for option in options:
            item = QListWidgetItem(option.label)
            item.setData(Qt.ItemDataRole.UserRole, option.id)
            if not option.active or option.has_active_assignment:
                item.setForeground(Qt.GlobalColor.darkGray)
            self.personnel_list.addItem(item)
            if option.id in selected_ids:
                item.setSelected(True)

    def _selected_personnel_options(self) -> tuple[PersonnelOption, ...]:
        return tuple(
            self._personnel_options[personnel_id]
            for personnel_id in (
                int(item.data(Qt.ItemDataRole.UserRole))
                for item in self.personnel_list.selectedItems()
            )
            if personnel_id in self._personnel_options
        )

    def _personnel_selection_changed(self) -> None:
        selected = self._selected_personnel_options()
        if not selected:
            self.personnel_address_label.setText(
                "Bir personel seçtiğinizde adresi burada görünür."
            )
            self._show_map(None)
            return
        if len(selected) == 1:
            self.personnel_number_edit.setText(selected[0].personnel_number)
        self.personnel_address_label.setText(
            "\n".join(
                f"{item.personnel_number} - {item.name}: {item.address_label}"
                for item in selected[:8]
            )
            + (f"\n… ve {len(selected) - 8} kişi daha" if len(selected) > 8 else "")
        )
        self.result_table.clearSelection()
        self._show_map(None, personnel=selected)

    def _distance(self) -> float:
        return float(self.distance_spin.value())

    def _preview_single(self) -> None:
        number = self.personnel_number_edit.text()
        self._manual_choices.clear()
        self._run_preview(
            lambda: self.service.preview_single(
                number, max_distance_meters=self._distance()
            )
        )

    def _preview_selected(self) -> None:
        ids = tuple(
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.personnel_list.selectedItems()
        )
        self._manual_choices.clear()
        self._run_preview(
            lambda: self.service.preview_selected(
                ids, max_distance_meters=self._distance()
            )
        )

    def _preview_all(self) -> None:
        self._manual_choices.clear()
        self._run_preview(
            lambda: self.service.preview_all_unassigned(
                max_distance_meters=self._distance()
            )
        )

    def _run_preview(self, function) -> None:
        if self._busy:
            return
        self._set_busy(True)
        worker = FunctionWorker(function)
        self._active_worker = worker
        worker.signals.result.connect(self._preview_ready)
        worker.signals.error.connect(self._preview_error)
        worker.signals.finished.connect(self._worker_finished)
        QThreadPool.globalInstance().start(worker)

    def _worker_finished(self) -> None:
        self._active_worker = None
        self._set_busy(False)

    def _preview_ready(self, preview: AssignmentPreview) -> None:
        self._preview = preview
        self.cancel_button.setEnabled(True)
        self._populate_filters()
        self._apply_filters()
        self._populate_needs()
        self.summary_label.setText(
            f"Toplam {len(preview.results)} / uygun {preview.assignable_count} / "
            f"atanamayan {preview.unassignable_count}"
        )
        self._update_commit_buttons()

    def _preview_error(self, message: str) -> None:
        QMessageBox.warning(self, "Atama Önizleme", message)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (
            self.single_button,
            self.selected_button,
            self.all_button,
            self.apply_alternative_button,
            self.commit_selected_button,
            self.commit_all_button,
        ):
            button.setEnabled(not busy)
        if not busy:
            self.apply_alternative_button.setEnabled(
                self.alternative_combo.count() > 0
            )
            self._update_commit_buttons()
        self.info_label.setText(
            "Uygunluk ve rota uzaklıkları hesaplanıyor…"
            if busy
            else "Değerlendirme öneri üretir; kalıcı atama yalnızca onay düğmesiyle yapılır."
        )

    def _populate_filters(self) -> None:
        assert self._preview is not None
        current_shift = self.shift_filter.currentData()
        current_collar = self.collar_filter.currentData()
        self.shift_filter.blockSignals(True)
        self.collar_filter.blockSignals(True)
        self.shift_filter.clear()
        self.shift_filter.addItem("Tüm vardiyalar", "")
        for value in sorted({item.shift for item in self._preview.results}):
            self.shift_filter.addItem(value, value)
        self.collar_filter.clear()
        self.collar_filter.addItem("Tüm yakalar", "")
        for value in sorted({item.collar for item in self._preview.results}):
            self.collar_filter.addItem(value, value)
        shift_index = self.shift_filter.findData(current_shift)
        collar_index = self.collar_filter.findData(current_collar)
        self.shift_filter.setCurrentIndex(max(0, shift_index))
        self.collar_filter.setCurrentIndex(max(0, collar_index))
        self.shift_filter.blockSignals(False)
        self.collar_filter.blockSignals(False)

    def _apply_filters(self) -> None:
        self.result_table.setRowCount(0)
        self._visible_results = []
        if self._preview is None:
            return
        status = self.status_filter.currentData()
        shift = self.shift_filter.currentData()
        collar = self.collar_filter.currentData()
        for result in self._preview.results:
            if status and result.status != status:
                continue
            if shift and result.shift != shift:
                continue
            if collar and result.collar != collar:
                continue
            self._visible_results.append(result)
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            values = (
                result.personnel_number,
                result.personnel_name,
                result.personnel_type,
                result.shift,
                result.collar,
                result.service_code or "-",
                result.route_name or "-",
                "-" if result.capacity is None else str(result.capacity),
                "-" if result.occupancy_before is None else f"{result.occupancy_before}/{result.occupancy_after}",
                "-" if result.remaining_capacity is None else str(result.remaining_capacity),
                "-" if result.distance_meters is None else f"{result.distance_meters:.0f} m",
                "-" if result.pickup_latitude is None else f"{result.pickup_latitude:.6f}, {result.pickup_longitude:.6f}",
                result.status,
                result.reason,
                str(len(result.alternatives)),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, result.personnel_id)
                self.result_table.setItem(row, column, item)
        self.alternative_combo.clear()
        self.apply_alternative_button.setEnabled(False)
        if self._visible_results:
            self.result_table.selectRow(0)
        else:
            self._show_map(None, personnel=self._selected_personnel_options())
        self._update_commit_buttons()

    def _result_selected(self) -> None:
        row = self.result_table.currentRow()
        if row < 0 or row >= len(self._visible_results):
            return
        result = self._visible_results[row]
        self._show_map(result)
        self.alternative_combo.clear()
        for alternative in result.alternatives:
            self.alternative_combo.addItem(alternative.label, alternative.relation_id)
        self.apply_alternative_button.setEnabled(
            not self._busy and self.alternative_combo.count() > 0
        )
        self._update_commit_buttons()

    def _apply_alternative(self) -> None:
        row = self.result_table.currentRow()
        relation_id = self.alternative_combo.currentData()
        if row < 0 or row >= len(self._visible_results) or relation_id is None:
            return
        person = self._visible_results[row]
        self._manual_choices[person.personnel_id] = int(relation_id)
        assert self._preview is not None
        ids = self._preview.personnel_ids
        self._run_preview(
            lambda: self.service.preview_selected(
                ids,
                max_distance_meters=self._distance(),
                manual_choices=dict(self._manual_choices),
            )
        )

    def _populate_needs(self) -> None:
        self.need_table.setRowCount(0)
        if self._preview is None:
            return
        for summary in self._preview.capacity_needs:
            row = self.need_table.rowCount()
            self.need_table.insertRow(row)
            values = (
                summary.shift,
                summary.collar,
                str(summary.personnel_count),
                str(summary.needed_capacity),
                summary.concentrated_areas,
                summary.suggested_route_or_area,
            )
            for column, value in enumerate(values):
                self.need_table.setItem(row, column, QTableWidgetItem(value))

    def _show_map(
        self,
        result: AssignmentPreviewResult | None,
        *,
        personnel: tuple[PersonnelOption, ...] = (),
    ) -> None:
        self.map_view.setHtml(
            render_assignment_preview_html(result, personnel=personnel)
        )

    def _update_commit_buttons(self) -> None:
        available = bool(
            self._preview
            and any(item.assignable for item in self._preview.results)
        )
        row = self.result_table.currentRow()
        selected_available = bool(
            available
            and 0 <= row < len(self._visible_results)
            and self._visible_results[row].assignable
        )
        self.commit_selected_button.setEnabled(not self._busy and selected_available)
        self.commit_all_button.setEnabled(not self._busy and available)

    def _commit_selected(self) -> None:
        row = self.result_table.currentRow()
        if row < 0 or row >= len(self._visible_results):
            return
        result = self._visible_results[row]
        if not result.assignable:
            return
        self._commit((result.personnel_id,))

    def _commit_all(self) -> None:
        if self._preview is None:
            return
        ids = tuple(
            item.personnel_id for item in self._preview.results if item.assignable
        )
        self._commit(ids)

    def _commit(self, personnel_ids: tuple[int, ...]) -> None:
        if self._preview is None or not personnel_ids:
            return
        answer = QMessageBox.question(
            self,
            "Kalıcı Personel Ataması",
            f"{len(personnel_ids)} personel, önizlemede gösterilen servis ve rotalara "
            "kalıcı olarak atanacak. Servis dolulukları artacaktır. Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            committed = self.service.commit_preview(
                self._preview,
                personnel_ids=personnel_ids,
            )
        except AssignmentPreviewError as error:
            QMessageBox.critical(self, "Personel Ataması", str(error))
            return
        assigned_ids = {item.personnel_id for item in committed.assignments}
        self._preview = replace(
            self._preview,
            results=tuple(
                replace(
                    item,
                    status="ATANDI",
                    reason="Kullanıcı onayıyla kalıcı atama yapıldı.",
                )
                if item.personnel_id in assigned_ids
                else item
                for item in self._preview.results
            ),
        )
        self.status_filter.setCurrentIndex(0)
        self._load_personnel()
        self._apply_filters()
        self.assignments_changed.emit()
        self.info_label.setText(
            f"{committed.assigned_count} personel kalıcı olarak atandı; servis doluluğu güncellendi."
        )
        QMessageBox.information(
            self,
            "Personel Ataması",
            f"{committed.assigned_count} personel başarıyla atandı. Servis Yönetimi > "
            "Servis Vardiyaları bölümünde doluluk ve personel listesini görebilirsiniz.",
        )

    def _save_distance(self) -> None:
        try:
            value = self.service.set_max_distance_meters(self._distance())
        except AssignmentPreviewError as error:
            QMessageBox.warning(self, "Yakınlık Sınırı", str(error))
            return
        self.info_label.setText(f"Varsayılan yakınlık sınırı {value:.0f} metre olarak kaydedildi.")

    def _cancel_preview(self) -> None:
        self._preview = None
        self._visible_results = []
        self._manual_choices.clear()
        self.result_table.setRowCount(0)
        self.need_table.setRowCount(0)
        self.alternative_combo.clear()
        self.apply_alternative_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.commit_selected_button.setEnabled(False)
        self.commit_all_button.setEnabled(False)
        self.summary_label.setText("Henüz önizleme yapılmadı.")
        self._show_map(None, personnel=self._selected_personnel_options())
