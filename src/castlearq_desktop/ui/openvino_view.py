from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QVBoxLayout, QWidget)
from castlearq_desktop.core.openvino_service import OpenVINOService
from castlearq_desktop.core.think_parser import split_think_output


_EMPTY_MODELS_TEXT = "Sin modelos OpenVINO disponibles"


class OpenVINOView(QWidget):
    def __init__(self, service: OpenVINOService, parent=None):
        super().__init__(parent)
        self._service = service
        self._service.completed.connect(self._on_completed)
        self._service.failed.connect(self._on_failed)
        self._service.busy_changed.connect(self._on_busy)
        self._raw_output = ""
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(400)
        self._think_timer.timeout.connect(self._tick_think)
        self._think_dots = 0
        box = QGroupBox("OpenVINO", self)
        layout = QVBoxLayout(box)
        self._model = QComboBox(box)
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
        self._think_indicator = QLabel("🧠 Think", box)
        self._think_indicator.setObjectName("thinkIndicator")
        self._think_indicator.setVisible(False)
        layout.addWidget(self._think_indicator)
        self._output = QTextEdit(box)
        self._output.setReadOnly(True)
        layout.addWidget(self._output)
        self._reasoning_toggle = QPushButton("Ver salida de razonamiento", box)
        self._reasoning_toggle.setCheckable(True)
        self._reasoning_toggle.setVisible(False)
        self._reasoning_toggle.toggled.connect(self._on_reasoning_toggled)
        layout.addWidget(self._reasoning_toggle)
        self._reasoning_output = QTextEdit(box)
        self._reasoning_output.setReadOnly(True)
        self._reasoning_output.setVisible(False)
        layout.addWidget(self._reasoning_output)
        self._status = QLabel("Listo.")
        layout.addWidget(self._status)
        outer = QVBoxLayout(self)
        outer.addWidget(box)

    def _on_run(self):
        model_id = self._model.currentText().strip()
        prompt = self._prompt.text()
        if not model_id or model_id == _EMPTY_MODELS_TEXT:
            self._status.setText("model_id es obligatorio.")
            return
        if not prompt.strip():
            self._status.setText("prompt es obligatorio.")
            return
        self._status.setText("Ejecutando inferencia OpenVINO…")
        self._service.infer(model_id, prompt, device=self._device.currentText(), max_tokens=512)

    def _on_completed(self, text, dev):
        self._stop_think()
        self._think_indicator.setVisible(False)
        split = split_think_output(text)
        self._raw_output = split.raw_output
        self._output.setPlainText(split.final_text)
        if split.has_reasoning:
            self._reasoning_toggle.setVisible(True)
            self._reasoning_toggle.setChecked(False)
            self._reasoning_output.setPlainText(split.reasoning_text)
            self._reasoning_output.setVisible(False)
        else:
            self._reasoning_toggle.setVisible(False)
            self._reasoning_output.setVisible(False)
            self._reasoning_output.clear()
        device_text = "No verificado" if dev == "unverified" else str(dev)
        status = "Completado"
        result = getattr(self._service, "last_result", None)
        finish_reason = getattr(result, "finish_reason", None) if result is not None else None
        if finish_reason:
            status = f"{status}. Motivo de finalización: {finish_reason}"
        status = f"{status}. Dispositivo: {device_text}"
        self._status.setText(status)

    def _on_failed(self, msg):
        self._stop_think()
        self._think_indicator.setVisible(False)
        self._status.setText(msg)

    def _on_busy(self, busy):
        self._run.setEnabled(not busy)
        if busy:
            self._status.setText("Ejecutando inferencia OpenVINO…")
            self._think_dots = 0
            self._think_indicator.setVisible(True)
            self._think_timer.start()
        else:
            self._stop_think()
            self._think_indicator.setVisible(False)

    def _tick_think(self):
        self._think_dots = (self._think_dots + 1) % 4
        dots = "." * self._think_dots
        self._think_indicator.setText(f"🧠 Think{dots}")

    def _stop_think(self):
        if self._think_timer.isActive():
            self._think_timer.stop()

    def _on_reasoning_toggled(self, checked):
        self._reasoning_output.setVisible(checked)

    def set_models(self, model_ids):
        self._model.clear()
        ids = [str(mid) for mid in (model_ids or []) if str(mid).strip()]
        if not ids:
            self._model.addItem(_EMPTY_MODELS_TEXT)
            item = self._model.model().item(0)
            if item is not None:
                item.setEnabled(False)
            self._model.setCurrentIndex(-1)
            return
        for mid in ids:
            self._model.addItem(mid)
        self._model.setCurrentIndex(0)
