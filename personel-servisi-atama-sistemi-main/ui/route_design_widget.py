"""Bağımsız Aşama 11 Rota Tasarımı ve kayıtlı rota yönetimi ekranı."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, QThreadPool, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.models import VardiyaTuru, YakaTuru
from services import ServiceManagementService
from services.route_design import (
    NewServiceForRoute,
    RouteCalculation,
    RouteDesignError,
    RouteDesignPoint,
    RouteDesignService,
    RouteSaveRequest,
    SavedRouteView,
)
from services.route_design_rendering import render_route_designer_html
from ui.workers import FunctionWorker


logger = logging.getLogger(__name__)


class _RouteMapBridge(QObject):
    trace_drawn = Signal(str)
    points_changed = Signal(str)

    @Slot(str)
    def traceDrawn(self, payload: str) -> None:
        self.trace_drawn.emit(payload)

    @Slot(str)
    def pointsChanged(self, payload: str) -> None:
        self.points_changed.emit(payload)


class RouteDesignWidget(QWidget):
    def __init__(
        self,
        parent=None,
        *,
        service: RouteDesignService | None = None,
        service_management: ServiceManagementService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or RouteDesignService()
        self.service_management = service_management or ServiceManagementService()
        self._points: tuple[RouteDesignPoint, ...] = ()
        self._calculation: RouteCalculation | None = None
        self._selected_route_id: int | None = None
        self._operation_in_progress = False
        self._map_sync_pending = False
        self._map_mode = "add"
        self._has_calculated_route = False
        self._active_worker: FunctionWorker | None = None
        self._build_ui()
        self._map_bridge = _RouteMapBridge(self)
        self._web_channel = QWebChannel(self.map_view.page())
        self._web_channel.registerObject("routeBridge", self._map_bridge)
        self.map_view.page().setWebChannel(self._web_channel)
        self._connect_signals()
        self.refresh_data()
        self._new_route()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)

        header = QFrame()
        header.setObjectName("routeDesignHeader")
        header.setStyleSheet(
            "#routeDesignHeader { background:#f5f7fa; border:1px solid #d8dde5; "
            "border-radius:6px; }"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 7, 10, 7)
        self.new_button = QPushButton("Yeni Rota")
        self.route_name_edit = QLineEdit()
        self.route_name_edit.setPlaceholderText("Örn. Mezitli Sabah Rotası")
        self.route_name_edit.setMinimumWidth(220)
        self.method_combo = QComboBox()
        self.method_combo.addItem("Haritada manuel rota", "MANUEL")
        self.method_combo.addItem("Adreslerden otomatik rota", "OTOMATIK")
        self.method_combo.setMinimumWidth(205)
        self.active_check = QCheckBox("Rota aktif")
        self.calculate_button = QPushButton("Rotayı Hesapla")
        self.calculate_button.setStyleSheet(
            "QPushButton { font-weight:600; padding:7px 14px; background:#1769aa; "
            "color:white; border-radius:4px; } "
            "QPushButton:disabled { background:#9aa5b1; color:#eef1f4; }"
        )
        self.save_button = QPushButton("Özeti Göster ve Kaydet")
        self.save_button.setStyleSheet("font-weight:600; padding:7px 12px;")
        header_layout.addWidget(self.new_button)
        header_layout.addWidget(QLabel("Rota Adı:"))
        header_layout.addWidget(self.route_name_edit, 2)
        header_layout.addWidget(QLabel("Yöntem:"))
        header_layout.addWidget(self.method_combo)
        header_layout.addWidget(self.active_check)
        header_layout.addStretch()
        header_layout.addWidget(self.calculate_button)
        header_layout.addWidget(self.save_button)
        root.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.control_tabs = QTabWidget()
        self.control_tabs.setMinimumWidth(350)
        self.control_tabs.setMaximumWidth(520)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        settings_page = QWidget()
        settings_root = QVBoxLayout(settings_page)
        settings_help = QLabel(
            "Rotayı mevcut servis-vardiyalara bağlayın veya onayınızla yeni bir "
            "servis ve ilk vardiya oluşturun. Ctrl tuşuyla birden fazla vardiya seçilebilir."
        )
        settings_help.setWordWrap(True)
        settings_help.setStyleSheet("color:#4c5665; padding:4px;")
        settings_root.addWidget(settings_help)

        service_group = QGroupBox("Servis ve Vardiya İlişkisi")
        service_layout = QVBoxLayout(service_group)
        self.shift_list = QListWidget()
        self.shift_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.shift_list.setMinimumHeight(180)
        service_layout.addWidget(QLabel("Mevcut servis-vardiyalar:"))
        service_layout.addWidget(self.shift_list)
        settings_root.addWidget(service_group)

        new_service_group = QGroupBox("İsteğe Bağlı Yeni Servis")
        new_service_form = QFormLayout(new_service_group)
        self.new_service_check = QCheckBox("Yeni fiziksel servis oluştur")
        self.service_code_edit = QLineEdit()
        self.shift_combo = QComboBox()
        for shift in VardiyaTuru:
            self.shift_combo.addItem(shift.value, shift.value)
        self.collar_combo = QComboBox()
        for collar in YakaTuru:
            self.collar_combo.addItem(collar.value, collar.value)
        self.capacity_spin = QSpinBox()
        self.capacity_spin.setRange(1, 10000)
        self.capacity_spin.setValue(16)
        new_service_form.addRow(self.new_service_check)
        new_service_form.addRow("Önerilen/Onaylanacak Kod:", self.service_code_edit)
        new_service_form.addRow("Vardiya:", self.shift_combo)
        new_service_form.addRow("Yaka:", self.collar_combo)
        new_service_form.addRow("Kapasite:", self.capacity_spin)
        settings_root.addWidget(new_service_group)
        settings_root.addStretch()
        settings_scroll.setWidget(settings_page)
        self.control_tabs.addTab(settings_scroll, "Rota Ayarları")

        points_page = QWidget()
        point_root = QVBoxLayout(points_page)
        self.manual_help = QLabel(
            "Haritaya sırayla tıklayın. İlk nokta başlangıçtır; işaretleri "
            "sürükleyebilirsiniz. Kırmızı nokta sabit Seçil Kauçuk bitişidir."
        )
        self.manual_help.setWordWrap(True)
        self.manual_help.setStyleSheet(
            "background:#eaf4ff; border:1px solid #b8d8f5; border-radius:5px; "
            "padding:8px; color:#174a73;"
        )
        point_root.addWidget(self.manual_help)
        self.point_table = QTableWidget(0, 6)
        self.point_table.setHorizontalHeaderLabels(
            ["Sıra", "Nokta/Adres", "Mahalle", "İlçe", "Enlem", "Boylam"]
        )
        self.point_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.point_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.point_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.point_table.horizontalHeader().setStretchLastSection(True)
        self.point_table.setMinimumHeight(210)
        point_root.addWidget(self.point_table, 1)

        point_name_row = QHBoxLayout()
        self.point_name_edit = QLineEdit()
        self.point_name_edit.setPlaceholderText(
            "Seçili noktaya belirleyici bir ad verin"
        )
        self.rename_point_button = QPushButton("Nokta Adını Güncelle")
        point_name_row.addWidget(QLabel("Seçili Noktanın Adı:"))
        point_name_row.addWidget(self.point_name_edit, 1)
        point_name_row.addWidget(self.rename_point_button)
        point_root.addLayout(point_name_row)

        common_point_buttons = QGridLayout()
        self.remove_point_button = QPushButton("Seçileni Sil")
        self.up_button = QPushButton("Yukarı Taşı")
        self.down_button = QPushButton("Aşağı Taşı")
        common_point_buttons.addWidget(self.remove_point_button, 0, 0)
        common_point_buttons.addWidget(self.up_button, 0, 1)
        common_point_buttons.addWidget(self.down_button, 0, 2)
        point_root.addLayout(common_point_buttons)

        self.point_entry_group = QGroupBox("Adres / Koordinat Noktası Ekle")
        entry_root = QVBoxLayout(self.point_entry_group)
        point_form = QFormLayout()
        self.point_edits: dict[str, QLineEdit] = {}
        for key, label, placeholder in (
            ("name", "Nokta Adı:", "Örn. Anayurt giriş durağı"),
            ("address", "Açık Adres:", "Bina/site/numara bilgisi"),
            ("neighborhood", "Mahalle:", "Mahalle"),
            ("street", "Sokak:", "Sokak"),
            ("avenue", "Cadde:", "Cadde"),
            ("district", "İlçe:", "İlçe"),
            ("city", "İl:", "İl"),
            ("latitude", "Enlem:", "İsteğe bağlı"),
            ("longitude", "Boylam:", "İsteğe bağlı"),
        ):
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            self.point_edits[key] = edit
            point_form.addRow(label, edit)
        entry_root.addLayout(point_form)
        entry_buttons = QHBoxLayout()
        self.add_point_button = QPushButton("Ekle / Seçilinin Ardına Ekle")
        self.update_point_button = QPushButton("Seçileni Güncelle")
        entry_buttons.addWidget(self.add_point_button)
        entry_buttons.addWidget(self.update_point_button)
        entry_root.addLayout(entry_buttons)
        self.accept_mismatch_check = QCheckBox(
            "Gösterilen adres uyuşmazlıklarını kontrol ettim ve onaylıyorum"
        )
        entry_root.addWidget(self.accept_mismatch_check)
        point_root.addWidget(self.point_entry_group)
        self.control_tabs.addTab(points_page, "Kontrol Noktaları")

        saved_page = QWidget()
        saved_layout = QVBoxLayout(saved_page)
        search = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Rota adıyla ara")
        self.search_button = QPushButton("Ara")
        self.refresh_button = QPushButton("Yenile")
        search.addWidget(self.search_edit, 1)
        search.addWidget(self.search_button)
        search.addWidget(self.refresh_button)
        saved_layout.addLayout(search)
        self.route_table = QTableWidget(0, 5)
        self.route_table.setHorizontalHeaderLabels(
            ["Rota", "Servis-Vardiya", "Yöntem", "Mesafe", "Durum"]
        )
        self.route_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.route_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.route_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.route_table.horizontalHeader().setStretchLastSection(True)
        saved_layout.addWidget(self.route_table, 1)
        saved_buttons = QGridLayout()
        self.load_button = QPushButton("Haritada Aç / Düzenle")
        self.toggle_button = QPushButton("Aktif/Pasif")
        self.delete_button = QPushButton("Kontrollü Sil")
        saved_buttons.addWidget(self.load_button, 0, 0, 1, 2)
        saved_buttons.addWidget(self.toggle_button, 1, 0)
        saved_buttons.addWidget(self.delete_button, 1, 1)
        saved_layout.addLayout(saved_buttons)
        self.control_tabs.addTab(saved_page, "Kayıtlı Rotalar")
        splitter.addWidget(self.control_tabs)

        map_group = QGroupBox("Rota Haritası")
        map_root = QVBoxLayout(map_group)
        map_top = QHBoxLayout()
        self.map_help_label = QLabel(
            "Haritaya tıklayarak kontrol noktası ekleyin; işaretleri sürükleyin."
        )
        self.map_help_label.setStyleSheet("font-weight:600; color:#344054;")
        self.sync_map_button = QPushButton("Harita Noktalarını Listeye Aktar")
        map_top.addWidget(self.map_help_label, 1)
        map_top.addWidget(self.sync_map_button)
        map_root.addLayout(map_top)
        map_modes = QHBoxLayout()
        map_modes.addWidget(QLabel("Harita aracı:"))
        self.map_mode_group = QButtonGroup(self)
        self.map_mode_group.setExclusive(True)
        self.pan_mode_button = QPushButton("Haritayı Gez")
        self.add_mode_button = QPushButton("Nokta Ekle")
        self.draw_mode_button = QPushButton("Çizerek Oluştur")
        self.delete_mode_button = QPushButton("Nokta Sil")
        for button in (
            self.pan_mode_button,
            self.add_mode_button,
            self.draw_mode_button,
            self.delete_mode_button,
        ):
            button.setCheckable(True)
            button.setStyleSheet(
                "QPushButton:checked { background:#1769aa; color:white; font-weight:600; }"
            )
            self.map_mode_group.addButton(button)
            map_modes.addWidget(button)
        self.add_mode_button.setChecked(True)
        self.undo_map_button = QPushButton("Son Noktayı Geri Al")
        self.clear_map_button = QPushButton("Tüm Noktaları Temizle")
        map_modes.addSpacing(8)
        map_modes.addWidget(self.undo_map_button)
        map_modes.addWidget(self.clear_map_button)
        map_modes.addStretch()
        map_root.addLayout(map_modes)
        self.map_view = QWebEngineView()
        self.map_view.setMinimumSize(620, 460)
        self.map_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        map_root.addWidget(self.map_view, 1)
        self.status_label = QLabel("Rota henüz hesaplanmadı")
        self.status_label.setStyleSheet(
            "background:#f7f7f8; border:1px solid #d8dde5; border-radius:4px; "
            "padding:7px; font-weight:600;"
        )
        map_root.addWidget(self.status_label)
        splitter.addWidget(map_group)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 1200])
        root.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.search_button.clicked.connect(self.refresh_data)
        self.search_edit.returnPressed.connect(self.refresh_data)
        self.refresh_button.clicked.connect(self.refresh_data)
        self.new_button.clicked.connect(self._new_route)
        self.load_button.clicked.connect(self._load_selected_route)
        self.route_table.itemDoubleClicked.connect(lambda _item: self._load_selected_route())
        self.toggle_button.clicked.connect(self._toggle_route)
        self.delete_button.clicked.connect(self._delete_route)
        self.method_combo.currentIndexChanged.connect(self._method_changed)
        self.new_service_check.toggled.connect(self._new_service_toggled)
        self.point_table.itemSelectionChanged.connect(self._load_selected_point)
        self.rename_point_button.clicked.connect(self._rename_selected_point)
        self.point_name_edit.returnPressed.connect(self._rename_selected_point)
        self.add_point_button.clicked.connect(self._add_point)
        self.update_point_button.clicked.connect(self._update_point)
        self.remove_point_button.clicked.connect(self._remove_point)
        self.up_button.clicked.connect(lambda: self._move_point(-1))
        self.down_button.clicked.connect(lambda: self._move_point(1))
        self.sync_map_button.clicked.connect(lambda: self._sync_from_map())
        self.pan_mode_button.clicked.connect(lambda: self._set_map_mode("pan"))
        self.add_mode_button.clicked.connect(lambda: self._set_map_mode("add"))
        self.draw_mode_button.clicked.connect(lambda: self._set_map_mode("draw"))
        self.delete_mode_button.clicked.connect(lambda: self._set_map_mode("delete"))
        self.undo_map_button.clicked.connect(self._undo_map_point)
        self.clear_map_button.clicked.connect(self._clear_map_points)
        self.map_view.loadFinished.connect(
            lambda successful: self._apply_map_mode() if successful else None
        )
        self._map_bridge.trace_drawn.connect(self._draw_trace_received)
        self._map_bridge.points_changed.connect(self._map_points_changed)
        self.calculate_button.clicked.connect(self._calculate_route)
        self.save_button.clicked.connect(self._save_route)

    def _method_changed(self) -> None:
        automatic = self.method_combo.currentData() == "OTOMATIK"
        self.point_entry_group.setVisible(automatic)
        self.accept_mismatch_check.setVisible(automatic)
        if automatic:
            self.manual_help.setText(
                "Adres alanlarını doldurup noktaları sırayla ekleyin. Koordinatları "
                "sistem çözer; hesaplamadan sonra işaretleri haritada sürükleyebilirsiniz."
            )
            self.map_help_label.setText(
                "Adres noktaları çözümlendiğinde burada görünür ve sürüklenebilir."
            )
        else:
            self.manual_help.setText(
                "Haritaya sırayla tıklayın. İlk nokta başlangıçtır; işaretleri "
                "sürükleyebilirsiniz. Kırmızı nokta sabit Seçil Kauçuk bitişidir."
            )
            self.map_help_label.setText(
                "Haritaya tıklayarak kontrol noktası ekleyin; işaretleri sürükleyin."
            )

    def refresh_data(self) -> None:
        try:
            routes = self.service.list_routes(self.search_edit.text())
            self._populate_routes(routes)
            self._populate_shifts()
        except RouteDesignError as error:
            self._show_error(str(error))

    def _populate_routes(self, routes: tuple[SavedRouteView, ...]) -> None:
        self.route_table.setRowCount(len(routes))
        for row, route in enumerate(routes):
            values = (
                route.route_name,
                ", ".join(route.shift_labels),
                "Manuel" if route.creation_method == "MANUEL" else "Otomatik",
                f"{route.distance_meters / 1000:.2f} km",
                "Aktif" if route.active else "Pasif",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, route.id)
                    item.setData(Qt.ItemDataRole.UserRole + 1, route.active)
                self.route_table.setItem(row, column, item)
        self.route_table.resizeColumnsToContents()

    def _populate_shifts(self) -> None:
        selected = set(self._selected_shift_ids())
        self.shift_list.clear()
        for shift in self.service_management.list_service_shifts():
            item = QListWidgetItem(
                f"{shift.service_code} / {shift.shift} / {shift.collar} / "
                f"kapasite {shift.capacity}"
            )
            item.setData(Qt.ItemDataRole.UserRole, shift.id)
            self.shift_list.addItem(item)
            item.setSelected(shift.id in selected)

    def _new_route(self) -> None:
        self._selected_route_id = None
        self._points = ()
        self._calculation = None
        self._has_calculated_route = False
        self.route_name_edit.clear()
        self.method_combo.setCurrentIndex(0)
        self._method_changed()
        self._set_map_mode("add")
        self.active_check.setChecked(True)
        self.new_service_check.setChecked(False)
        self._new_service_toggled(False)
        self.shift_list.clearSelection()
        self.accept_mismatch_check.setChecked(False)
        self._clear_point_form()
        self.point_name_edit.clear()
        self._refresh_points()
        try:
            self.service_code_edit.setText(self.service.suggest_service_code())
        except RouteDesignError:
            self.service_code_edit.clear()
        self._render_map()
        self.status_label.setText("Yeni rota — önce kontrol noktalarını oluşturun")
        self._refresh_action_buttons()

    def _selected_route_table_id(self) -> int | None:
        rows = self.route_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.route_table.item(rows[0].row(), 0)
        return None if item is None else int(item.data(Qt.ItemDataRole.UserRole))

    def _load_selected_route(self) -> None:
        route_id = self._selected_route_table_id()
        if route_id is None:
            return
        try:
            route = self.service.get_route(route_id)
            destination = self.service.factory_destination()
            self._selected_route_id = route.id
            self._points = route.points
            self._calculation = RouteCalculation(
                points=route.points,
                destination=destination,
                distance_meters=route.distance_meters,
                duration_seconds=route.duration_seconds,
                geometry=route.geometry,
            )
            self._has_calculated_route = True
            self.route_name_edit.setText(route.route_name)
            self.method_combo.setCurrentIndex(
                self.method_combo.findData(route.creation_method)
            )
            self._method_changed()
            self.active_check.setChecked(route.active)
            self.new_service_check.setChecked(False)
            for index in range(self.shift_list.count()):
                item = self.shift_list.item(index)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) in route.shift_ids)
            self._refresh_points()
            self._set_map_mode("pan")
            self._render_map()
            self._update_result_label()
            self._refresh_action_buttons()
        except RouteDesignError as error:
            self._show_error(str(error))

    def _selected_point_index(self) -> int | None:
        rows = self.point_table.selectionModel().selectedRows()
        return None if not rows else rows[0].row()

    def _point_from_form(self) -> RouteDesignPoint:
        value = lambda key: self.point_edits[key].text().strip() or None
        return RouteDesignPoint(
            name=value("name"),
            address=value("address"),
            neighborhood=value("neighborhood"),
            street=value("street"),
            avenue=value("avenue"),
            district=value("district"),
            city=value("city"),
            latitude=value("latitude"),
            longitude=value("longitude"),
            coordinate_status=(
                "MANUEL" if value("latitude") or value("longitude") else "EKSIK"
            ),
        )

    def _add_point(self) -> None:
        point = self._point_from_form()
        index = self._selected_point_index()
        insertion = len(self._points) if index is None else index + 1
        try:
            self._points = self.service.insert_point(self._points, insertion, point)
            self._points_changed()
        except RouteDesignError as error:
            self._show_error(str(error))

    def _update_point(self) -> None:
        index = self._selected_point_index()
        if index is None:
            return
        try:
            self._points = self.service.update_point(
                self._points, index, self._point_from_form()
            )
            self._points_changed()
        except RouteDesignError as error:
            self._show_error(str(error))

    def _remove_point(self) -> None:
        index = self._selected_point_index()
        if index is None:
            return
        self._points = self.service.delete_point(self._points, index)
        self._points_changed()

    def _move_point(self, direction: int) -> None:
        index = self._selected_point_index()
        if index is None:
            return
        try:
            self._points = self.service.move_point(self._points, index, direction)
            self._points_changed(select_index=index + direction)
        except RouteDesignError as error:
            self._show_error(str(error))

    def _points_changed(self, *, select_index: int | None = None) -> None:
        self._calculation = None
        self._refresh_points()
        self._render_map()
        self.status_label.setText("Kontrol noktaları değişti — rotayı yeniden hesaplayın")
        self._refresh_action_buttons()
        if select_index is not None and 0 <= select_index < len(self._points):
            self.point_table.selectRow(select_index)

    def _refresh_points(self) -> None:
        self.point_table.setRowCount(len(self._points))
        for row, point in enumerate(self._points):
            values = (
                str(row + 1),
                point.name or point.address or "Harita noktası",
                point.neighborhood or "",
                point.district or "",
                "" if point.latitude is None else str(point.latitude),
                "" if point.longitude is None else str(point.longitude),
            )
            for column, value in enumerate(values):
                self.point_table.setItem(row, column, QTableWidgetItem(value))
        self.point_table.resizeColumnsToContents()

    def _load_selected_point(self) -> None:
        index = self._selected_point_index()
        if index is None or index >= len(self._points):
            self.point_name_edit.clear()
            return
        point = self._points[index]
        self.point_name_edit.setText(point.name or "")
        values = {
            "name": point.name,
            "address": point.address,
            "neighborhood": point.neighborhood,
            "street": point.street,
            "avenue": point.avenue,
            "district": point.district,
            "city": point.city,
            "latitude": point.latitude,
            "longitude": point.longitude,
        }
        for key, value in values.items():
            self.point_edits[key].setText("" if value is None else str(value))

    def _rename_selected_point(self) -> None:
        index = self._selected_point_index()
        if index is None:
            QMessageBox.information(
                self,
                "Kontrol Noktası",
                "Önce listeden adını değiştirmek istediğiniz noktayı seçin.",
            )
            return
        name = self.point_name_edit.text().strip()
        if not name:
            self._show_error("Nokta adı boş bırakılamaz.")
            return
        self._points = self.service.update_point(
            self._points,
            index,
            replace(self._points[index], name=name),
        )
        self._points_changed(select_index=index)

    def _clear_point_form(self) -> None:
        for edit in self.point_edits.values():
            edit.clear()

    def _sync_from_map(self, callback=None) -> None:
        self._set_map_sync_pending(True)
        script = (
            "JSON.stringify(window.routeDesigner "
            "? {ready:true, points:window.routeDesigner.getPoints()} "
            ": {ready:false, points:[]})"
        )
        self.map_view.page().runJavaScript(
            script,
            lambda data: self._map_points_received(data, callback),
        )

    def _map_points_received(self, data, callback=None) -> None:
        self._set_map_sync_pending(False)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        ready = isinstance(data, dict) and bool(data.get("ready"))
        map_points = data.get("points") if ready else None
        if not isinstance(map_points, list):
            self.status_label.setText("Harita noktaları okunamadı")
            self._show_error(
                "Harita henüz hazır değil veya noktalar okunamadı. "
                "Harita yüklendikten sonra yeniden deneyin."
            )
            return
        self._points = self._route_points_from_map_data(map_points)
        self._calculation = None
        self._refresh_points()
        self._refresh_action_buttons()
        if callback is not None:
            callback()

    def _map_points_changed(self, payload: str) -> None:
        if self._operation_in_progress or self._map_sync_pending:
            self._render_map()
            return
        try:
            map_points = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            self._show_error("Haritadaki değişen noktalar okunamadı.")
            return
        if not isinstance(map_points, list):
            self._show_error("Haritadaki değişen noktalar geçersiz.")
            return
        self._points = self._route_points_from_map_data(map_points)
        self._calculation = None
        self._refresh_points()
        self.status_label.setText(
            "Kontrol noktaları değişti — rotayı yeniden hesaplayın"
        )
        self._refresh_action_buttons()

    def _route_points_from_map_data(self, map_points: list) -> tuple[RouteDesignPoint, ...]:
        synced: list[RouteDesignPoint] = []
        for value in map_points:
            if not isinstance(value, dict):
                continue
            source_index = value.get("sourceIndex")
            old = (
                self._points[source_index]
                if isinstance(source_index, int)
                and 0 <= source_index < len(self._points)
                else RouteDesignPoint()
            )
            synced.append(
                RouteDesignPoint(
                    name=old.name or value.get("name"),
                    address=old.address,
                    neighborhood=old.neighborhood,
                    street=old.street,
                    avenue=old.avenue,
                    district=old.district,
                    city=old.city,
                    latitude=value.get("latitude"),
                    longitude=value.get("longitude"),
                    coordinate_status="MANUEL",
                    active=old.active,
                )
            )
        return tuple(synced)

    def _draw_trace_received(self, payload: str) -> None:
        if self._operation_in_progress or self._map_sync_pending:
            self._render_map()
            QMessageBox.information(
                self,
                "İşlem Devam Ediyor",
                "Devam eden işlem tamamlanınca çizimi yeniden deneyin.",
            )
            return
        try:
            map_points = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            self._render_map()
            self._show_error("Haritadaki çizim noktaları okunamadı.")
            return
        if not isinstance(map_points, list):
            self._render_map()
            self._show_error("Haritadaki çizim noktaları geçersiz.")
            return
        candidate_points = self._route_points_from_map_data(map_points)
        if len(candidate_points) < 2:
            self._render_map()
            self._show_error("Çizerek rota oluşturmak için en az iki nokta gerekir.")
            return
        self.status_label.setText("Çizim sürülebilir yol ağına oturtuluyor…")
        self._run_async(
            lambda: self.service.calculate_drawn_trace(candidate_points),
            self._route_calculated,
            on_error=self._draw_trace_failed,
        )

    def _draw_trace_failed(self, message: str) -> None:
        self._render_map()
        self.status_label.setText("Çizim kabul edilmedi — önceki rota korundu")
        self._refresh_action_buttons()
        self._show_error(message)

    def _set_map_mode(self, mode: str) -> None:
        if mode not in {"pan", "add", "draw", "delete"}:
            return
        self._map_mode = mode
        buttons = {
            "pan": self.pan_mode_button,
            "add": self.add_mode_button,
            "draw": self.draw_mode_button,
            "delete": self.delete_mode_button,
        }
        buttons[mode].setChecked(True)
        help_text = {
            "pan": "Haritayı sürükleyerek gezin; mevcut işaretler yine taşınabilir.",
            "add": "Haritaya her tıklama yeni bir sıralı kontrol noktası ekler.",
            "draw": (
                "Sol fare tuşunu basılı tutup yolların üzerinden çizin. Bırakınca "
                "iz en fazla 25 noktaya indirilir ve yakın sürülebilir "
                "yollara oturtulur; yolsuz bölüm varsa çizim reddedilir."
            ),
            "delete": (
                "Silmek istediğiniz işarete tıklayın. İşarete sağ tıklamak da "
                "doğrudan siler."
            ),
        }
        self.map_help_label.setText(help_text[mode])
        self._apply_map_mode()

    def _apply_map_mode(self) -> None:
        self.map_view.page().runJavaScript(
            f"window.routeDesigner && window.routeDesigner.setMode('{self._map_mode}')"
        )

    def _undo_map_point(self) -> None:
        self.map_view.page().runJavaScript(
            "window.routeDesigner && window.routeDesigner.undoLast()",
            lambda _result: self._sync_from_map(),
        )

    def _clear_map_points(self) -> None:
        answer = QMessageBox.question(
            self,
            "Harita Noktalarını Temizle",
            "Haritadaki bütün kontrol noktaları kaldırılsın mı?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.map_view.page().runJavaScript(
            "window.routeDesigner && window.routeDesigner.clearPoints()",
            lambda _result: self._sync_from_map(),
        )

    def _calculate_route(self) -> None:
        if self._operation_in_progress or self._map_sync_pending:
            return
        method = str(self.method_combo.currentData())

        def start() -> None:
            if method == "MANUEL":
                operation = lambda: self.service.calculate_manual(self._points)
            else:
                operation = lambda: self.service.calculate_automatic(
                    self._points,
                    accept_mismatches=self.accept_mismatch_check.isChecked(),
                )
            self._run_async(operation, self._route_calculated)

        if method == "MANUEL" or all(
            point.latitude is not None and point.longitude is not None
            for point in self._points
        ):
            self._sync_from_map(start)
        else:
            start()

    def _route_calculated(self, calculation: RouteCalculation) -> None:
        self._calculation = calculation
        self._has_calculated_route = True
        self._points = calculation.points
        self._refresh_points()
        self._render_map()
        self._update_result_label()
        self._refresh_action_buttons()

    def _save_request(self, *, confirmed: bool) -> RouteSaveRequest:
        if self._calculation is None:
            raise RouteDesignError("Kaydetmeden önce rotayı hesaplayın.")
        new_service = None
        if self.new_service_check.isChecked():
            new_service = NewServiceForRoute(
                service_code=self.service_code_edit.text(),
                shift=str(self.shift_combo.currentData()),
                collar=str(self.collar_combo.currentData()),
                capacity=self.capacity_spin.value(),
                service_code_confirmed=confirmed,
            )
        return RouteSaveRequest(
            route_name=self.route_name_edit.text(),
            creation_method=str(self.method_combo.currentData()),
            calculation=self._calculation,
            shift_ids=() if new_service else self._selected_shift_ids(),
            active=self.active_check.isChecked(),
            new_service=new_service,
        )

    def _save_route(self) -> None:
        try:
            preliminary = self._save_request(confirmed=False)
            confirmed = False
            if preliminary.new_service is not None:
                answer = QMessageBox.question(
                    self,
                    "Yeni Servis Kodu Onayı",
                    f"'{preliminary.new_service.service_code}' koduyla yeni fiziksel servis "
                    "ve vardiya oluşturulsun mu?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                confirmed = True
            request = self._save_request(confirmed=confirmed)
            summary = self.service.build_summary(request)
            answer = QMessageBox.question(
                self,
                "Rota Kayıt Özeti",
                f"Rota: {summary.route_name}\n"
                f"Servis/Vardiya: {', '.join(summary.service_shifts)}\n"
                f"Kapasite: {summary.capacity}\n"
                f"Mesafe: {summary.distance_km:.2f} km\n"
                f"Tahmini süre: {summary.duration_minutes:.1f} dakika\n"
                f"Kontrol noktası: {summary.point_count}\n\nKaydedilsin mi?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._run_async(
                lambda: self.service.save_route(
                    request, route_id=self._selected_route_id
                ),
                self._route_saved,
            )
        except RouteDesignError as error:
            self._show_error(str(error))

    def _route_saved(self, route: SavedRouteView) -> None:
        self._selected_route_id = route.id
        self.refresh_data()
        QMessageBox.information(self, "Rota Kaydı", "Rota başarıyla kaydedildi.")

    def _toggle_route(self) -> None:
        route_id = self._selected_route_table_id()
        rows = self.route_table.selectionModel().selectedRows()
        if route_id is None or not rows:
            return
        active = bool(
            self.route_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole + 1)
        )
        self._run_async(
            lambda: self.service.set_route_active(route_id, not active),
            lambda _result: self.refresh_data(),
        )

    def _delete_route(self) -> None:
        route_id = self._selected_route_table_id()
        if route_id is None:
            return
        if QMessageBox.question(
            self,
            "Kontrollü Rota Silme",
            "Kullanılmamış rota kalıcı silinir; geçmiş ilişkisi bulunan rota yalnızca "
            "pasif yapılır. Devam edilsin mi?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run_async(
            lambda: self.service.delete_route_controlled(route_id),
            self._route_deleted,
        )

    def _route_deleted(self, result) -> None:
        self.refresh_data()
        QMessageBox.information(self, "Rota Silme", result.message)

    def _selected_shift_ids(self) -> tuple[int, ...]:
        return tuple(
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.shift_list.selectedItems()
        )

    def _new_service_toggled(self, checked: bool) -> None:
        self.shift_list.setEnabled(not checked)
        for widget in (
            self.service_code_edit,
            self.shift_combo,
            self.collar_combo,
            self.capacity_spin,
        ):
            widget.setEnabled(checked)
        if checked and not self.service_code_edit.text():
            self.service_code_edit.setText(self.service.suggest_service_code())

    def _render_map(self) -> None:
        destination = self.service.factory_destination()
        geometry = None if self._calculation is None else self._calculation.geometry
        self.map_view.setHtml(
            render_route_designer_html(self._points, destination, geometry)
        )

    def _update_result_label(self) -> None:
        if self._calculation is None:
            self.status_label.setText("Rota henüz hesaplanmadı")
            return
        self.status_label.setText(
            f"{self._calculation.distance_meters / 1000:.2f} km / "
            f"{self._calculation.duration_seconds / 60:.1f} dk / "
            f"Bitiş: {self._calculation.destination.name}"
        )

    def _run_async(self, function, on_result, *, on_error=None) -> bool:
        if self._operation_in_progress:
            QMessageBox.information(
                self, "İşlem Devam Ediyor", "Devam eden işlem tamamlanmadan yenisi başlatılamaz."
            )
            return False
        worker = FunctionWorker(function)
        error_handler = on_error or self._show_error

        def result_received(result) -> None:
            self._set_busy(False)
            on_result(result)

        def error_received(message: str) -> None:
            self._set_busy(False)
            error_handler(message)

        def worker_finished() -> None:
            self._active_worker = None
            if self._operation_in_progress:
                self._set_busy(False)

        worker.signals.result.connect(result_received)
        worker.signals.error.connect(error_received)
        worker.signals.finished.connect(worker_finished)
        self._active_worker = worker
        self._set_busy(True)
        QThreadPool.globalInstance().start(worker)
        return True

    def _set_busy(self, busy: bool) -> None:
        self._operation_in_progress = busy
        self._refresh_action_buttons()

    def _refresh_action_buttons(self) -> None:
        idle = not self._operation_in_progress and not self._map_sync_pending
        route_stale = self._calculation is None
        self.calculate_button.setText(
            "Rotayı Yeniden Hesapla"
            if self._has_calculated_route and route_stale
            else "Rotayı Hesapla"
        )
        self.calculate_button.setEnabled(idle and bool(self._points) and route_stale)
        self.save_button.setEnabled(idle and self._calculation is not None)
        self.save_button.setToolTip(
            ""
            if self.save_button.isEnabled()
            else "Önce güncel rotayı başarıyla hesaplayın."
        )
        self.delete_button.setEnabled(idle)
        self.toggle_button.setEnabled(idle)

    def _set_map_sync_pending(self, pending: bool) -> None:
        self._map_sync_pending = pending
        self._set_busy(self._operation_in_progress)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Rota Tasarımı Hatası", message)
