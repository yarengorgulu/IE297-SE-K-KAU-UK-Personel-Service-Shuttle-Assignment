"""Servis, servis-vardiya ve şoför yönetimi için sekmeli kapsayıcı."""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from services import ServiceManagementService
from ui.driver_management_widget import DriverManagementWidget
from ui.route_management_widget import RouteManagementWidget
from ui.service_list_management_widget import ServiceListManagementWidget
from ui.service_shift_management_widget import ServiceShiftManagementWidget


class ServiceManagementWidget(QWidget):
    def __init__(
        self,
        parent=None,
        *,
        service: ServiceManagementService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or ServiceManagementService()
        self.service_widget = ServiceListManagementWidget(service=self.service)
        self.shift_widget = ServiceShiftManagementWidget(service=self.service)
        self.driver_widget = DriverManagementWidget(service=self.service)
        self.route_widget = RouteManagementWidget(service_management=self.service)
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        tabs.addTab(self.service_widget, "Fiziksel Servisler")
        tabs.addTab(self.shift_widget, "Servis Vardiyaları")
        tabs.addTab(self.driver_widget, "Şoförler")
        tabs.addTab(self.route_widget, "Güzergâhlar")
        layout.addWidget(tabs)

    def _connect_signals(self) -> None:
        for widget in (
            self.service_widget,
            self.shift_widget,
            self.driver_widget,
            self.route_widget,
        ):
            widget.data_changed.connect(self.refresh_all)

    def refresh_all(self) -> None:
        self.service_widget.refresh_data()
        self.shift_widget.refresh_data()
        self.driver_widget.refresh_data()
        self.route_widget.refresh_data()
