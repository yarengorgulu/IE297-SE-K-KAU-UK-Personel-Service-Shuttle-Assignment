"""Uygulamanın temel ana penceresi."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    APPLICATION_DISPLAY_NAME,
    APPLICATION_ICON_PATH,
    APPLICATION_LOGO_PATH,
)
from ui.assignment_preview_widget import AssignmentPreviewWidget
from ui.excel_import_widget import ExcelImportWidget
from ui.map_management_widget import MapManagementWidget
from ui.personnel_management_widget import PersonnelManagementWidget
from ui.route_design_widget import RouteDesignWidget
from ui.service_management_widget import ServiceManagementWidget


class MainWindow(QMainWindow):
    """Sonraki modüllerin yerleştirileceği temel uygulama penceresi."""

    def __init__(self, database_path: Path) -> None:
        super().__init__()
        self.database_path = database_path
        self._configure_window()
        self._build_content()

    def _configure_window(self) -> None:
        self.setWindowTitle(APPLICATION_DISPLAY_NAME)
        if APPLICATION_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APPLICATION_ICON_PATH)))
        self.resize(1100, 700)
        self.setMinimumSize(800, 500)

    def _build_content(self) -> None:
        central_widget = QWidget(self)
        layout = QVBoxLayout(central_widget)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 8, 0)
        left_spacer = QWidget()
        left_spacer.setFixedWidth(245)
        title = QLabel(APPLICATION_DISPLAY_NAME)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.brand_logo = QLabel()
        self.brand_logo.setFixedSize(245, 70)
        self.brand_logo.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if APPLICATION_LOGO_PATH.exists():
            pixmap = QPixmap(str(APPLICATION_LOGO_PATH))
            self.brand_logo.setPixmap(
                pixmap.scaled(
                    self.brand_logo.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        header_layout.addWidget(left_spacer)
        header_layout.addWidget(title, 1)
        header_layout.addWidget(self.brand_logo)

        tabs = QTabWidget(central_widget)
        tabs.addTab(ExcelImportWidget(tabs), "Excel Aktarımı")
        tabs.addTab(PersonnelManagementWidget(tabs), "Personel Yönetimi")
        service_management = ServiceManagementWidget(tabs)
        tabs.addTab(service_management, "Servis Yönetimi")
        tabs.addTab(MapManagementWidget(tabs), "Harita ve Rota")
        tabs.addTab(RouteDesignWidget(tabs), "Rota Tasarımı")
        assignment_preview = AssignmentPreviewWidget(tabs)
        assignment_preview.assignments_changed.connect(service_management.refresh_all)
        tabs.addTab(assignment_preview, "Personel Atama")

        layout.addWidget(header)
        layout.addWidget(tabs, 1)

        self.setCentralWidget(central_widget)
        self.statusBar().showMessage("Hazır")
