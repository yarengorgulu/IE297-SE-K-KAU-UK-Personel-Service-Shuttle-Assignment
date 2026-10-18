"""Güzergâh Excel aktarım planı onay penceresi."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from services import ServiceRouteImportPlan


class ServiceRouteImportDialog(QDialog):
    def __init__(self, plan: ServiceRouteImportPlan, parent=None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("Güzergâh Aktarım Onayı")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        summary = QLabel(
            "Güzergâh aktarım planı:\n"
            f"Eklenecek nokta: {plan.add_count}\n"
            f"Atlanacak eşleşme: {plan.skip_count}\n"
            f"Hatalı eşleşme: {plan.invalid_count}\n"
            f"Mükerrer nokta: {plan.duplicate_count}\n"
            f"Bulunmayan servis: {plan.missing_service_count}"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        details = QPlainTextEdit()
        details.setReadOnly(True)
        detail_lines = list(plan.warning_details[:200])
        if len(plan.warning_details) > len(detail_lines):
            detail_lines.append(
                f"... {len(plan.warning_details) - len(detail_lines)} ek ayrıntı gösterilmedi."
            )
        details.setPlainText("\n".join(detail_lines) or "Uyarı veya hata bulunmuyor.")
        layout.addWidget(details, 1)

        note = QLabel(
            "Vardiya/yaka belirtilmemişse aynı güzergâh noktası servisin tüm "
            "servis-vardiyalarına ayrı ayrı eklenir. Belirtilmişse yalnızca eşleşen "
            "vardiyalara eklenir. Servis kaydı otomatik oluşturulmaz ve aktarım tek "
            "transaction içinde yapılır."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aktar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Vazgeç")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
