"""Personel konumu, güzergâh haritası, geocoding ve rota hesaplama ekranı."""

from __future__ import annotations

import logging

from PySide6.QtCore import QThreadPool
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services import (
    BulkGeocodingResult,
    CoordinateManagementService,
    CoordinateTargetView,
    GeocodingLookupResult,
    MapManagementError,
    PersonnelManagementError,
    PersonnelManagementService,
    RouteCalculationResult,
    RouteCalculationService,
    RouteMapData,
    ServiceManagementError,
    ServiceManagementService,
)
from services.map_rendering import render_map_html
from ui.geocoding_dialogs import CoordinateDialog, GeocodingResultDialog
from ui.workers import FunctionWorker, ProgressFunctionWorker


logger = logging.getLogger(__name__)


class MapManagementWidget(QWidget):
    PRIVACY_WARNING = (
        "Adres bilgisi koordinat çözümleme amacıyla yapılandırılmış Nominatim "
        "servisine gönderilecektir. Personel adresleri kişisel veri içerebilir.\n\n"
        "İşleme devam etmek istiyor musunuz?"
    )

    def __init__(
        self,
        parent=None,
        *,
        personnel_service: PersonnelManagementService | None = None,
        service_management: ServiceManagementService | None = None,
        coordinate_service: CoordinateManagementService | None = None,
        route_service: RouteCalculationService | None = None,
    ) -> None:
        super().__init__(parent)
        self.personnel_service = personnel_service or PersonnelManagementService()
        self.service_management = service_management or ServiceManagementService(
            self.personnel_service.session_factory
        )
        self.coordinate_service = coordinate_service or CoordinateManagementService(
            self.personnel_service.session_factory
        )
        self.route_service = route_service or RouteCalculationService(
            self.personnel_service.session_factory
        )
        self._operation_in_progress = False
        self._current_worker: FunctionWorker | ProgressFunctionWorker | None = None
        self._shown_personnel: CoordinateTargetView | None = None
        self._shown_route: RouteMapData | None = None
        self._build_ui()
        self._connect_signals()
        self.refresh_data()
        self._render_map()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        personnel_group = QGroupBox("Personel Konumu")
        personnel_layout = QHBoxLayout(personnel_group)
        self.personnel_combo = QComboBox()
        self.show_personnel_button = QPushButton("Personeli Haritada Göster")
        self.geocode_personnel_button = QPushButton("Adresi Çözümle")
        self.manual_personnel_button = QPushButton("Manuel Koordinat")
        self.bulk_personnel_button = QPushButton("Eksik Personelleri Toplu Çöz")
        personnel_layout.addWidget(QLabel("Personel:"))
        personnel_layout.addWidget(self.personnel_combo, 1)
        personnel_layout.addWidget(self.show_personnel_button)
        personnel_layout.addWidget(self.geocode_personnel_button)
        personnel_layout.addWidget(self.manual_personnel_button)
        personnel_layout.addWidget(self.bulk_personnel_button)
        root.addWidget(personnel_group)

        route_group = QGroupBox("Servis-Vardiya Güzergâhı")
        route_root = QVBoxLayout(route_group)
        route_selection = QHBoxLayout()
        self.service_combo = QComboBox()
        self.shift_combo = QComboBox()
        self.point_combo = QComboBox()
        route_selection.addWidget(QLabel("Fiziksel Servis:"))
        route_selection.addWidget(self.service_combo, 1)
        route_selection.addWidget(QLabel("Servis-Vardiya:"))
        route_selection.addWidget(self.shift_combo, 2)
        route_selection.addWidget(QLabel("Güzergâh Noktası:"))
        route_selection.addWidget(self.point_combo, 2)
        route_root.addLayout(route_selection)
        factory_layout = QHBoxLayout()
        self.factory_destination_label = QLabel("Sabit Varış: Seçil Kauçuk")
        self.factory_coordinate_button = QPushButton("Fabrika Koordinatını Düzenle")
        factory_layout.addWidget(self.factory_destination_label, 1)
        factory_layout.addWidget(self.factory_coordinate_button)
        route_root.addLayout(factory_layout)
        route_buttons = QHBoxLayout()
        self.show_route_button = QPushButton("Güzergâhı Haritada Göster")
        self.geocode_point_button = QPushButton("Seçili Noktayı Çözümle")
        self.manual_point_button = QPushButton("Manuel Koordinat")
        self.bulk_point_button = QPushButton("Eksik Noktaları Toplu Çöz")
        self.calculate_route_button = QPushButton("Rotayı Hesapla")
        for button in (
            self.show_route_button,
            self.geocode_point_button,
            self.manual_point_button,
            self.bulk_point_button,
            self.calculate_route_button,
        ):
            route_buttons.addWidget(button)
        route_root.addLayout(route_buttons)
        root.addWidget(route_group)

        status_layout = QHBoxLayout()
        self.status_label = QLabel("Hazır")
        self.route_result_label = QLabel("Rota henüz hesaplanmadı")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.cancel_button = QPushButton("İptal Et")
        self.cancel_button.setVisible(False)
        status_layout.addWidget(self.status_label, 2)
        status_layout.addWidget(self.route_result_label, 2)
        status_layout.addWidget(self.progress_bar, 2)
        status_layout.addWidget(self.cancel_button)
        root.addLayout(status_layout)

        self.map_view = QWebEngineView()
        root.addWidget(self.map_view, 1)

    def _connect_signals(self) -> None:
        self.service_combo.currentIndexChanged.connect(self._service_changed)
        self.shift_combo.currentIndexChanged.connect(self._shift_changed)
        self.show_personnel_button.clicked.connect(self._show_personnel)
        self.show_route_button.clicked.connect(self._show_route)
        self.geocode_personnel_button.clicked.connect(
            lambda: self._geocode_selected("PERSONEL")
        )
        self.geocode_point_button.clicked.connect(
            lambda: self._geocode_selected("GUZERGAH")
        )
        self.manual_personnel_button.clicked.connect(
            lambda: self._manual_coordinates("PERSONEL")
        )
        self.manual_point_button.clicked.connect(
            lambda: self._manual_coordinates("GUZERGAH")
        )
        self.bulk_personnel_button.clicked.connect(
            lambda: self._start_bulk("PERSONEL")
        )
        self.bulk_point_button.clicked.connect(lambda: self._start_bulk("GUZERGAH"))
        self.calculate_route_button.clicked.connect(self._calculate_route)
        self.factory_coordinate_button.clicked.connect(
            self._edit_factory_coordinates
        )
        self.cancel_button.clicked.connect(self._cancel_operation)
        self.map_view.loadFinished.connect(self._map_loaded)

    def refresh_data(self) -> None:
        selected_personnel = self.personnel_combo.currentData()
        selected_service = self.service_combo.currentData()
        try:
            self.personnel_combo.clear()
            for personnel in self.personnel_service.list_personnel():
                label = f"{personnel.personnel_number} - {personnel.first_name or ''} {personnel.last_name or ''}".strip()
                self.personnel_combo.addItem(label, personnel.id)
            self._set_combo_value(self.personnel_combo, selected_personnel)
            self.service_combo.blockSignals(True)
            self.service_combo.clear()
            for service in self.service_management.list_services():
                self.service_combo.addItem(service.service_code, service.id)
            self._set_combo_value(self.service_combo, selected_service)
            self.service_combo.blockSignals(False)
            self._service_changed()
            self._refresh_factory_destination()
        except (PersonnelManagementError, ServiceManagementError) as error:
            self._show_error(str(error))

    def _service_changed(self) -> None:
        selected_shift = self.shift_combo.currentData()
        self.shift_combo.blockSignals(True)
        self.shift_combo.clear()
        service_id = self.service_combo.currentData()
        if service_id is not None:
            try:
                for shift in self.service_management.list_service_shifts(int(service_id)):
                    self.shift_combo.addItem(
                        f"{shift.shift} / {shift.collar} / {shift.route_name or 'Rota adı yok'}",
                        shift.id,
                    )
            except ServiceManagementError as error:
                self._show_error(str(error))
        self._set_combo_value(self.shift_combo, selected_shift)
        self.shift_combo.blockSignals(False)
        self._shift_changed()

    def _shift_changed(self) -> None:
        self.point_combo.clear()
        shift_id = self.shift_combo.currentData()
        if shift_id is None:
            return
        try:
            data = self.route_service.get_shift_map_data(int(shift_id))
            for point in data.points:
                if point.is_destination:
                    continue
                status = "" if point.latitude is not None and point.longitude is not None else " (Koordinat eksik)"
                self.point_combo.addItem(
                    f"{point.sequence}. {point.label}{status}",
                    point.id,
                )
            self._update_route_result(data)
        except MapManagementError as error:
            self._show_error(str(error))

    def _show_personnel(self) -> None:
        personnel_id = self.personnel_combo.currentData()
        if personnel_id is None:
            return
        try:
            self._shown_personnel = self.coordinate_service.get_personnel_target(
                int(personnel_id)
            )
            self._render_map()
            if not self._shown_personnel.has_coordinates:
                QMessageBox.information(
                    self,
                    "Personel Konumu",
                    "Seçilen personelin koordinatı henüz bulunmuyor.",
                )
        except MapManagementError as error:
            self._show_error(str(error))

    def _show_route(self) -> None:
        shift_id = self.shift_combo.currentData()
        if shift_id is None:
            return
        try:
            self._shown_route = self.route_service.get_shift_map_data(int(shift_id))
            self._update_route_result(self._shown_route)
            self._render_map()
        except MapManagementError as error:
            self._show_error(str(error))

    def _geocode_selected(self, target_type: str) -> None:
        target_id = (
            self.personnel_combo.currentData()
            if target_type == "PERSONEL"
            else self.point_combo.currentData()
        )
        if target_id is None or self._operation_in_progress:
            return
        try:
            target = (
                self.coordinate_service.get_personnel_target(int(target_id))
                if target_type == "PERSONEL"
                else self.coordinate_service.get_route_target(int(target_id))
            )
        except MapManagementError as error:
            self._show_error(str(error))
            return
        force = target.has_coordinates
        if force:
            answer = QMessageBox.question(
                self,
                "Koordinatı Yeniden Çözümle",
                "Bu kaydın koordinatı zaten mevcut. Adresi yeniden sorgulamak istiyor musunuz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if not self._confirm_privacy():
            return
        if target_type == "PERSONEL":
            operation = lambda: self.coordinate_service.search_personnel(
                int(target_id),
                force=force,
            )
        else:
            operation = lambda: self.coordinate_service.search_route_point(
                int(target_id),
                force=force,
            )
        self._run_async(
            operation,
            lambda result: self._geocoding_ready(target_type, int(target_id), result),
        )

    def _geocoding_ready(
        self,
        target_type: str,
        target_id: int,
        result: GeocodingLookupResult,
    ) -> None:
        if not result.candidates:
            QMessageBox.warning(
                self,
                "Koordinat Bulunamadı",
                "Adres için koordinat bulunamadı. Kayıt değiştirilmedi; manuel koordinat girebilirsiniz.",
            )
            return
        dialog = GeocodingResultDialog(result, self)
        if not dialog.exec():
            return
        candidate = dialog.selected_candidate()
        if candidate is None:
            return
        try:
            if target_type == "PERSONEL":
                updated = self.coordinate_service.confirm_personnel_candidate(
                    target_id,
                    candidate,
                    accept_found_address=dialog.accept_found_address(),
                )
                self._shown_personnel = updated
            else:
                self.coordinate_service.confirm_route_candidate(
                    target_id,
                    candidate,
                    accept_found_address=dialog.accept_found_address(),
                )
                self._shown_route = self.route_service.get_shift_map_data(
                    int(self.shift_combo.currentData())
                )
            self._shift_changed()
            self._render_map()
            QMessageBox.information(self, "Koordinat", "Seçilen koordinat kaydedildi.")
        except MapManagementError as error:
            self._show_error(str(error))

    def _manual_coordinates(self, target_type: str) -> None:
        target_id = (
            self.personnel_combo.currentData()
            if target_type == "PERSONEL"
            else self.point_combo.currentData()
        )
        if target_id is None or self._operation_in_progress:
            return
        try:
            target = (
                self.coordinate_service.get_personnel_target(int(target_id))
                if target_type == "PERSONEL"
                else self.coordinate_service.get_route_target(int(target_id))
            )
            dialog = CoordinateDialog(
                latitude=target.latitude,
                longitude=target.longitude,
                parent=self,
            )
            if not dialog.exec():
                return
            latitude, longitude = dialog.coordinates()
            if target_type == "PERSONEL":
                self._shown_personnel = self.coordinate_service.set_personnel_coordinates(
                    int(target_id), latitude, longitude
                )
            else:
                self.coordinate_service.set_route_coordinates(
                    int(target_id), latitude, longitude
                )
                self._shown_route = self.route_service.get_shift_map_data(
                    int(self.shift_combo.currentData())
                )
            self._shift_changed()
            self._render_map()
        except MapManagementError as error:
            self._show_error(str(error))

    def _start_bulk(self, target_type: str) -> None:
        if self._operation_in_progress or not self._confirm_privacy():
            return
        question = (
            "Koordinatı eksik veya güncel olmayan personeller sırayla çözümlenecek. "
            if target_type == "PERSONEL"
            else "Koordinatı eksik veya güncel olmayan güzergâh noktaları sırayla çözümlenecek. "
        )
        answer = QMessageBox.question(
            self,
            "Toplu Koordinat Çözümleme",
            question
            + "İstekler saniyede en fazla bir kez gönderilir. Devam etmek istiyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        function = (
            self.coordinate_service.bulk_geocode_personnel
            if target_type == "PERSONEL"
            else self.coordinate_service.bulk_geocode_route_points
        )
        self._run_progress(function, self._bulk_completed)

    def _bulk_completed(self, result: BulkGeocodingResult) -> None:
        self.refresh_data()
        QMessageBox.information(
            self,
            "Toplu Koordinat Çözümleme",
            f"Toplam kayıt: {result.total_count}\n"
            f"Başarılı: {result.success_count}\n"
            f"Koordinat bulunamadı: {result.not_found_count}\n"
            f"Kullanıcı seçimi gereken: {result.warning_count}\n"
            f"Teknik hata: {result.failure_count}\n"
            f"İptal edildi: {'Evet' if result.cancelled else 'Hayır'}",
        )

    def _calculate_route(self) -> None:
        shift_id = self.shift_combo.currentData()
        if shift_id is None or self._operation_in_progress:
            return
        self._run_async(
            lambda: self.route_service.calculate_shift(int(shift_id)),
            self._route_calculated,
        )

    def _refresh_factory_destination(self) -> None:
        try:
            destination = self.route_service.get_factory_destination()
            self.factory_destination_label.setText(
                f"Sabit Varış: {destination.name} — "
                f"{destination.address}, {destination.district}/{destination.city} — "
                f"{destination.latitude:.6f}, {destination.longitude:.6f}"
            )
        except MapManagementError as error:
            self.factory_destination_label.setText(
                f"Sabit fabrika varış ayarı okunamadı: {error}"
            )

    def _edit_factory_coordinates(self) -> None:
        if self._operation_in_progress:
            return
        try:
            destination = self.route_service.get_factory_destination()
            dialog = CoordinateDialog(
                latitude=destination.latitude,
                longitude=destination.longitude,
                parent=self,
            )
            dialog.setWindowTitle("Seçil Kauçuk Fabrika Koordinatı")
            if not dialog.exec():
                return
            latitude, longitude = dialog.coordinates()
            self.route_service.set_factory_coordinates(latitude, longitude)
            self._refresh_factory_destination()
            shift_id = self.shift_combo.currentData()
            if shift_id is not None:
                self._shown_route = self.route_service.get_shift_map_data(int(shift_id))
                self._update_route_result(self._shown_route)
                self._render_map()
            QMessageBox.information(
                self,
                "Fabrika Varış Noktası",
                "Seçil Kauçuk koordinatı kaydedildi. Mevcut rotalar güncel değil "
                "olarak işaretlendi.",
            )
        except MapManagementError as error:
            self._show_error(str(error))

    def _route_calculated(self, result: RouteCalculationResult) -> None:
        self._shown_route = result.map_data
        self._update_route_result(result.map_data)
        self._render_map()
        QMessageBox.information(
            self,
            "Rota Hesaplandı",
            f"Toplam mesafe: {result.distance_meters / 1000:.2f} km\n"
            f"Tahmini süre: {result.duration_seconds / 60:.1f} dakika\n"
            "Toplama sırası optimize edildi; son durak Seçil Kauçuk.",
        )

    def _run_async(self, function, on_result) -> bool:
        if self._operation_in_progress:
            return False
        worker = FunctionWorker(function)
        self._current_worker = worker
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(self._operation_error)
        worker.signals.finished.connect(self._operation_finished)
        self._set_busy(True)
        QThreadPool.globalInstance().start(worker)
        return True

    def _run_progress(self, function, on_result) -> bool:
        if self._operation_in_progress:
            return False
        worker = ProgressFunctionWorker(function)
        self._current_worker = worker
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(self._operation_error)
        worker.signals.progress.connect(self._progress_changed)
        worker.signals.finished.connect(self._operation_finished)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._set_busy(True, cancellable=True)
        QThreadPool.globalInstance().start(worker)
        return True

    def _progress_changed(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Koordinat çözümleme: {current}/{total}")

    def _cancel_operation(self) -> None:
        if isinstance(self._current_worker, ProgressFunctionWorker):
            self._current_worker.cancel()
            self.status_label.setText("İptal isteği alındı; mevcut istek tamamlanıyor…")
            self.cancel_button.setEnabled(False)

    def _operation_finished(self) -> None:
        self._current_worker = None
        self._set_busy(False)

    def _operation_error(self, message: str) -> None:
        self._show_error(message)

    def _set_busy(self, busy: bool, *, cancellable: bool = False) -> None:
        self._operation_in_progress = busy
        for button in (
            self.geocode_personnel_button,
            self.manual_personnel_button,
            self.bulk_personnel_button,
            self.geocode_point_button,
            self.manual_point_button,
            self.bulk_point_button,
            self.factory_coordinate_button,
            self.calculate_route_button,
        ):
            button.setEnabled(not busy)
        self.progress_bar.setVisible(busy and cancellable)
        self.cancel_button.setVisible(busy and cancellable)
        self.cancel_button.setEnabled(busy and cancellable)
        if not busy:
            self.status_label.setText("Hazır")

    def _update_route_result(self, data: RouteMapData) -> None:
        if data.distance_meters is None or data.duration_seconds is None:
            self.route_result_label.setText("Rota henüz hesaplanmadı")
            return
        status = "Güncel" if data.route_is_current else "Güncel değil — yeniden hesaplayın"
        self.route_result_label.setText(
            f"{data.distance_meters / 1000:.2f} km / "
            f"{data.duration_seconds / 60:.1f} dk / {status}"
        )

    def _render_map(self) -> None:
        personnel = () if self._shown_personnel is None else (self._shown_personnel,)
        self.map_view.setHtml(
            render_map_html(personnel=personnel, route=self._shown_route),
            "https://servis-atama.local/",
        )

    def _map_loaded(self, success: bool) -> None:
        if not success:
            self.status_label.setText(
                "Harita yüklenemedi; internet bağlantısını kontrol edin."
            )

    def _confirm_privacy(self) -> bool:
        return (
            QMessageBox.warning(
                self,
                "Adres Verisi Gönderimi",
                self.PRIVACY_WARNING,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Harita ve Rota İşlemi Hatası", message)
