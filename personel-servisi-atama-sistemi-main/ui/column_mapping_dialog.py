"""Basit, genişletilebilir manuel sütun eşleştirme penceresi."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from excel_import.mapping import build_column_mapping
from normalization.columns import COLUMN_ALIASES
from normalization.result import RecordType


class ColumnMappingDialog(QDialog):
    def __init__(
        self,
        headers: Sequence[str],
        record_type: RecordType,
        overrides: Mapping[int, str | None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.headers = tuple(headers)
        self.record_type = record_type
        self.setWindowTitle("Sütun Eşleştirmeyi Düzenle")
        self.resize(720, 460)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Her Excel sütununu bir hedef alana bağlayabilir veya fazladan sütun "
                "olarak koruyabilirsiniz."
            )
        )

        plan = build_column_mapping(self.headers, record_type, overrides)
        self.table = QTableWidget(len(plan.entries), 3, self)
        self.table.setHorizontalHeaderLabels(
            ["Excel sütunu", "Standart başlık", "Hedef alan"]
        )
        self._combos: list[QComboBox] = []
        targets = tuple(COLUMN_ALIASES[record_type])

        for row_index, entry in enumerate(plan.entries):
            self.table.setItem(row_index, 0, QTableWidgetItem(entry.source_header))
            self.table.setItem(row_index, 1, QTableWidgetItem(entry.normalized_header))
            combo = QComboBox(self.table)
            combo.addItem("Fazladan sütun olarak koru", None)
            for target in targets:
                combo.addItem(target, target)
            selected_index = combo.findData(entry.target_field)
            combo.setCurrentIndex(max(selected_index, 0))
            self.table.setCellWidget(row_index, 2, combo)
            self._combos.append(combo)

        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mapping_overrides(self) -> dict[int, str | None]:
        return {
            index: combo.currentData()
            for index, combo in enumerate(self._combos)
        }
