"""Servis kapasite aktarımı öncesi özet ve çakışma seçimi penceresi."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
)

from services import ConflictBehavior, ServiceCapacityImportPlan


class ServiceCapacityImportDialog(QDialog):
    def __init__(self, plan: ServiceCapacityImportPlan, parent=None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("Servis Kapasite Aktarımı Onayı")
        self.resize(620, 560)
        self._build_ui()
        self._update_conflict_counts()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Aşağıdaki özet veritabanının mevcut durumuna göre hazırlanmıştır. "
                "Onay verdiğinizde yalnızca doğrulanmış servis kapasite kayıtları "
                "transaction içinde aktarılacaktır."
            )
        )

        summary_group = QGroupBox("Aktarım Özeti")
        summary_layout = QFormLayout(summary_group)
        summary_layout.addRow("Toplam geçerli kayıt:", QLabel(str(self.plan.valid_record_count)))
        summary_layout.addRow("Yeni fiziksel servis:", QLabel(str(self.plan.new_service_count)))
        summary_layout.addRow("Yeni servis vardiyası:", QLabel(str(self.plan.new_shift_count)))
        summary_layout.addRow("Çakışan satır:", QLabel(str(self.plan.conflict_count)))
        self.update_count_label = QLabel()
        self.skip_count_label = QLabel()
        summary_layout.addRow("Güncellenecek kayıt:", self.update_count_label)
        summary_layout.addRow("Atlanacak kayıt:", self.skip_count_label)
        summary_layout.addRow("Hatalı kayıt:", QLabel(str(self.plan.invalid_count)))
        summary_layout.addRow("Uyarılı geçerli kayıt:", QLabel(str(self.plan.warning_record_count)))
        layout.addWidget(summary_group)

        conflict_group = QGroupBox("Mevcut Kayıt Çakışması")
        conflict_layout = QVBoxLayout(conflict_group)
        self.skip_radio = QRadioButton("Mevcut kaydı koru ve bu satırı atla")
        self.update_radio = QRadioButton(
            "Mevcut kaydın kapasite, rota adı ve aktiflik bilgilerini güncelle"
        )
        self.skip_radio.setChecked(True)
        self.skip_radio.toggled.connect(self._update_conflict_counts)
        self.update_radio.toggled.connect(self._update_conflict_counts)
        conflict_layout.addWidget(self.skip_radio)
        conflict_layout.addWidget(self.update_radio)
        if self.plan.conflict_count == 0:
            self.skip_radio.setEnabled(False)
            self.update_radio.setEnabled(False)
        layout.addWidget(conflict_group)

        details = QTextEdit()
        details.setReadOnly(True)
        detail_lines: list[str] = []
        if self.plan.warning_details:
            detail_lines.append(
                "UYARILI KAYITLAR (onay verirseniz aktarılacak):"
            )
            detail_lines.extend(f"- {detail}" for detail in self.plan.warning_details)
        if self.plan.invalid_details:
            if detail_lines:
                detail_lines.append("")
            detail_lines.append("AKTARILMAYACAK HATALI KAYITLAR:")
            detail_lines.extend(f"- {detail}" for detail in self.plan.invalid_details)
        details.setPlainText(
            "\n".join(detail_lines)
            if detail_lines
            else "Uyarılı veya hatalı kayıt bulunmuyor."
        )
        layout.addWidget(details, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aktarımı Başlat")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Vazgeç")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_conflict_counts(self) -> None:
        update_selected = self.update_radio.isChecked()
        self.update_count_label.setText(
            str(self.plan.conflict_count if update_selected else 0)
        )
        self.skip_count_label.setText(
            str(0 if update_selected else self.plan.conflict_count)
        )

    def conflict_behavior(self) -> ConflictBehavior:
        return (
            ConflictBehavior.UPDATE
            if self.update_radio.isChecked()
            else ConflictBehavior.SKIP
        )
