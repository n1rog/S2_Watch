from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QFormLayout,
)

from s2_watch.models import ANY_APPLICATION, WatchForm


class WatchFormDialog(QDialog):
    def __init__(
        self,
        parent=None,
        form: WatchForm | None = None,
        applications: tuple[str, ...] = (
            ANY_APPLICATION, "JS8Call", "MagCommstat", "Commstat", "FLMSG", "JS8SuperSpotter"
        ),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit watch form" if form else "Add watch form")
        self.application = QComboBox()
        self.application.addItems(applications)
        if form:
            index = self.application.findText(form.application)
            if index >= 0:
                self.application.setCurrentIndex(index)
        self.name = QLineEdit(form.name if form else "")
        self.match_text = QLineEdit(form.match_text if form else "")
        self.match_text.setPlaceholderText("Optional - blank matches all messages")
        self.message_type = QLineEdit(form.message_type if form else "")
        self.sender = QLineEdit(form.sender if form else "")
        self.use_regex = QCheckBox("Use regular expression")
        self.use_regex.setChecked(form.use_regex if form else False)

        layout = QFormLayout(self)
        layout.addRow(QLabel("Application"), self.application)
        layout.addRow(QLabel("Form name"), self.name)
        layout.addRow(QLabel("Text to match (optional)"), self.match_text)
        layout.addRow(QLabel("Message type"), self.message_type)
        layout.addRow(QLabel("Sender/call-sign"), self.sender)
        layout.addRow(self.use_regex)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def watch_form(self) -> WatchForm:
        return WatchForm(
            application=self.application.currentText(),
            name=self.name.text().strip(),
            match_text=self.match_text.text(),
            use_regex=self.use_regex.isChecked(),
            message_type=self.message_type.text().strip(),
            sender=self.sender.text().strip(),
        )

    def accept(self) -> None:
        if not self.name.text().strip():
            self.name.setFocus()
            return
        super().accept()
