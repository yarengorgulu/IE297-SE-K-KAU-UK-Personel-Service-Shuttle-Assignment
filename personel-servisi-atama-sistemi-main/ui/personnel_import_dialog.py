"""Personel aktarımı öncesi özet ve sicil çakışması seçimi penceresi."""

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

from services import ConflictBehavior, PersonnelImportPlan


class PersonnelImportDialog(QDialog):
    def __init__(self, plan: PersonnelImportPlan, parent=None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("Personel Aktarımı Onayı")
        self.resize(620, 560)
        self._build_ui()
        self._update_conflict_counts()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Aşağıdaki özet mevcut personel kayıtlarına göre hazırlanmıştır. "
                "Onay verdiğinizde yalnızca doğrulanmış personel kayıtları tek "
                "transaction içinde aktarılacaktır."
            )
        )

        summary_group = QGroupBox("Aktarım Özeti")
        summary_layout = QFormLayout(summary_group)
        summary_layout.addRow("Toplam geçerli kayıt:", QLabel(str(self.plan.valid_record_count)))
        summary_layout.addRow("Yeni personel:", QLabel(str(self.plan.new_personnel_count)))
        summary_layout.addRow("Çakışan sicil:", QLabel(str(self.plan.conflict_count)))
        self.update_count_label = QLabel()
        self.skip_count_label = QLabel()
        summary_layout.addRow("Güncellenecek personel:", self.update_count_label)
        summary_layout.addRow("Atlanacak kayıt:", self.skip_count_label)
        summary_layout.addRow("Hatalı kayıt:", QLabel(str(self.plan.invalid_count)))
        summary_layout.addRow("Uyarılı geçerli kayıt:", QLabel(str(self.plan.warning_record_count)))
        layout.addWidget(summary_group)

        conflict_group = QGroupBox("Sicil Numarası Çakışması")
        conflict_layout = QVBoxLayout(conflict_group)
        self.skip_radio = QRadioButton("Mevcut personeli koru ve bu satırı atla")
        self.update_radio = QRadioButton(
            "Mevcut personelin profil, adres, vardiya, yaka ve aktiflik bilgilerini güncelle"
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
        lines: list[str] = []
        if self.plan.warning_details:
            lines.append("UYARILI KAYITLAR (onay verirseniz aktarılacak):")
            lines.extend(f"- {detail}" for detail in self.plan.warning_details)
        if self.plan.invalid_details:
            if lines:
                lines.append("")
            lines.append("AKTARILMAYACAK HATALI KAYITLAR:")
            lines.extend(f"- {detail}" for detail in self.plan.invalid_details)
        details.setPlainText(
            "\n".join(lines) if lines else "Uyarılı veya hatalı kayıt bulunmuyor."
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
