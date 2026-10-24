from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QVBoxLayout, QWidget)
from castlearq_desktop.core.openvino_service import OpenVINOService


class OpenVINOView(QWidget):
    def __init__(self, service: OpenVINOService, parent=None):
        super().__init__(parent)
        self._service = service
        self._service.completed.connect(self._on_completed)
        self._service.failed.connect(self._on_failed)
        self._service.busy_changed.connect(self._on_busy)
        box = QGroupBox("OpenVINO", self)
        layout = QVBoxLayout(box)
        self._model = QLineEdit(box)
        self._model.setPlaceholderText("model_id")
        layout.addWidget(self._model)
        row = QHBoxLayout()
        self._device = QComboBox(box)
        self._device.addItems(["CPU", "GPU"])
        self._device.setToolTip("Dispositivo solicitado; la compatibilidad real depende de Core.")
        row.addWidget(self._device)
        self._run = QPushButton("Run", box)
        self._run.clicked.connect(self._on_run)
        row.addWidget(self._run)
        layout.addLayout(row)
        self._prompt = QLineEdit(box)
        self._prompt.setPlaceholderText("prompt")
        layout.addWidget(self._prompt)
        self._output = QTextEdit(box)
        self._output.setReadOnly(True)
        layout.addWidget(self._output)
        self._status = QLabel("Listo.")
        layout.addWidget(self._status)
        outer = QVBoxLayout(self)
        outer.addWidget(box)

    def _on_run(self):
        model_id = self._model.text().strip()
        prompt = self._prompt.text()
        if not model_id:
            self._status.setText("model_id es obligatorio.")
            return
        if not prompt.strip():
            self._status.setText("prompt es obligatorio.")
            return
        self._status.setText("Ejecutando inferencia OpenVINO…")
        self._service.infer(model_id, prompt, device=self._device.currentText(), max_tokens=8)

    def _on_completed(self, text, dev):
        self._output.setPlainText(text)
        self._status.setText(f"Completado ({dev}).")

    def _on_failed(self, msg):
        self._status.setText(msg)

    def _on_busy(self, busy):
        self._run.setEnabled(not busy)
        if busy:
            self._status.setText("Ejecutando inferencia OpenVINO…")
