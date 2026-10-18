"""Adres sonucu seçimi ve manuel koordinat giriş pencereleri."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services import CandidateReview, GeocodingCandidate, GeocodingLookupResult


class GeocodingResultDialog(QDialog):
    def __init__(self, result: GeocodingLookupResult, parent=None) -> None:
        super().__init__(parent)
        self.result = result
        self.setWindowTitle("Adres Sonucu Seçimi")
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Sonuçlar adres uyumu, sorgu ayrıntısı, ters adres kontrolü ve mümkünse "
            "mahalle alanına göre sıralanır. Uyuşmazlık varsa mevcut bilgiler "
            "kendiliğinden değiştirilmez."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.table = QTableWidget(len(result.candidates), 4)
        self.table.setHorizontalHeaderLabels(
            ["Bulunan adres", "Enlem", "Boylam", "Doğrulama durumu"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, review in enumerate(result.candidates):
            control_parts = [f"{review.status} — uyum puanı {review.score}/100"]
            control_parts.extend(review.details)
            if review.mismatches:
                control_parts.extend(item.message for item in review.mismatches)
            control_text = "; ".join(dict.fromkeys(control_parts))
            values = (
                review.candidate.display_name,
                str(review.candidate.latitude),
                str(review.candidate.longitude),
                control_text,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        if result.candidates:
            self.table.selectRow(0)
        layout.addWidget(self.table, 1)

        self.accept_address_check = QCheckBox(
            "Seçilen sonuçtaki il, ilçe, mahalle, sokak ve cadde bilgilerini de kabul et"
        )
        layout.addWidget(self.accept_address_check)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Seçimi Onayla")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Vazgeç")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_review(self) -> CandidateReview | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self.result.candidates):
            return self.result.candidates[index]
        return None

    def selected_candidate(self) -> GeocodingCandidate | None:
        review = self.selected_review()
        return None if review is None else review.candidate

    def accept_found_address(self) -> bool:
        return self.accept_address_check.isChecked()


class CoordinateDialog(QDialog):
    def __init__(
        self,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manuel Koordinat Girişi")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.latitude_edit = QLineEdit("" if latitude is None else str(latitude))
        self.longitude_edit = QLineEdit("" if longitude is None else str(longitude))
        self.latitude_edit.setPlaceholderText("-90 ile 90 arasında")
        self.longitude_edit.setPlaceholderText("-180 ile 180 arasında")
        form.addRow("Enlem:", self.latitude_edit)
        form.addRow("Boylam:", self.longitude_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Kaydet")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Vazgeç")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def coordinates(self) -> tuple[str, str]:
        return self.latitude_edit.text(), self.longitude_edit.text()
