from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget)
from castlearq_desktop.core.openvino_service import OpenVINOService
from castlearq_desktop.core.think_parser import split_think_output


_EMPTY_MODELS_TEXT = "Sin modelos OpenVINO disponibles"
_DEFAULT_MAX_TOKENS = 1024
_MIN_MAX_TOKENS = 1
_MAX_MAX_TOKENS = 2048


class OpenVINOView(QWidget):
    def __init__(self, service: OpenVINOService, parent=None):
        super().__init__(parent)
        self._service = service
        self._service.completed.connect(self._on_completed)
        self._service.failed.connect(self._on_failed)
        self._service.busy_changed.connect(self._on_busy)
        self._raw_output = ""
        self._history: list[tuple[str, str]] = []
        self._history_model: Optional[str] = None
        self._max_history_turns = 10
        self._pending_user_text: Optional[str] = None
        self._pending_model_id: Optional[str] = None
        self._pending_model_format: Optional[str] = None
        self._model_formats: dict[str, str] = {}
        self._ambiguous_model_ids: set[str] = set()
        self._inference_supported = False
        self._selected_format: Optional[str] = None
        self._models_loading = False
        self._models_error: Optional[str] = None
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(400)
        self._think_timer.timeout.connect(self._tick_think)
        self._think_dots = 0
        box = QGroupBox("OpenVINO", self)
        layout = QVBoxLayout(box)
        self._model = QComboBox(box)
        self._model.setPlaceholderText("Selecciona un modelo OpenVINO")
        layout.addWidget(self._model)
        self._model_status = QLabel("Sin modelo seleccionado.", box)
        layout.addWidget(self._model_status)
        row = QHBoxLayout()
        self._device = QComboBox(box)
        self._device.addItems(["CPU", "GPU"])
        self._device.setToolTip("Dispositivo solicitado; la compatibilidad real depende de Core.")
        row.addWidget(self._device)
        self._device_status = QLabel("Dispositivo solicitado: CPU", box)
        layout.addWidget(self._device_status)
        self._max_tokens_label = QLabel("Máx tokens:", box)
        row.addWidget(self._max_tokens_label)
        self._max_tokens = QSpinBox(box)
        self._max_tokens.setRange(_MIN_MAX_TOKENS, _MAX_MAX_TOKENS)
        self._max_tokens.setValue(_DEFAULT_MAX_TOKENS)
        self._max_tokens.setToolTip("Máximo de nuevos tokens a generar (1-2048).")
        row.addWidget(self._max_tokens)
        self._run = QPushButton("Run", box)
        self._run.clicked.connect(self._on_run)
        row.addWidget(self._run)
        self._model.currentIndexChanged.connect(self._on_model_changed)
        self._device.currentIndexChanged.connect(self._update_controls)
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
        self.set_models([])

    def reset_conversation(self) -> None:
        self._history = []
        self._pending_user_text = None

    def _build_prompt(self, model_id: str, current: str) -> str:
        if self._history_model != model_id:
            self._history = []
            self._history_model = model_id
        blocks = []
        for user_text, assistant_text in self._history[-self._max_history_turns:]:
            blocks.append(f"<|im_start|>user\n{user_text}<|im_end|>\n")
            blocks.append(f"<|im_start|>assistant\n{assistant_text}<|im_end|>\n")
        blocks.append(f"<|im_start|>user\n{current}<|im_end|>\n")
        blocks.append("<|im_start|>assistant\n")
        return "".join(blocks)

    def _on_run(self):
        model_id = self._model.currentText().strip()
        current = self._prompt.text()
        if not model_id or model_id == _EMPTY_MODELS_TEXT:
            self._status.setText("model_id es obligatorio.")
            return
        if not current.strip():
            self._status.setText("prompt es obligatorio.")
            return
        if not self._inference_supported:
            self._status.setText("La inferencia OpenVINO integrada no está disponible para la versión de Core confirmada.")
            return
        if model_id in self._ambiguous_model_ids:
            self._status.setText(
                "Core no permite identificar inequívocamente el archivo seleccionado: "
                "hay varios artefactos con este model_id."
            )
            return
        max_tokens = self._max_tokens.value()
        if not (_MIN_MAX_TOKENS <= max_tokens <= _MAX_MAX_TOKENS):
            self._status.setText(f"max_tokens debe estar entre {_MIN_MAX_TOKENS} y {_MAX_MAX_TOKENS}.")
            return
        fmt = self._model_formats.get(model_id)
        if fmt is None and self._pending_model_id == model_id:
            fmt = self._pending_model_format or self._selected_format
        if fmt != "OpenVINO IR":
            self._status.setText(
                f"El artefacto '{model_id}' no tiene un formato OpenVINO IR admitido "
                f"(formato actual: {fmt or 'desconocido'})."
            )
            return
        self._status.setText("Ejecutando inferencia OpenVINO…")
        prompt = self._build_prompt(model_id, current)
        self._pending_user_text = current
        self._service.infer(model_id, prompt, device=self._device.currentText(), max_tokens=max_tokens)

    def _on_completed(self, text, dev):
        self._stop_think()
        self._think_indicator.setVisible(False)
        split = split_think_output(text)
        self._raw_output = split.raw_output
        has_unclosed_or_malformed_think = ("<think>" in split.final_text) or ("</think>" in split.final_text)

        result = getattr(self._service, "last_result", None)
        finish_reason = getattr(result, "finish_reason", None) if result is not None else None

        # Salida incompleta o malformada:
        # 1) Bloque think no cerrado o malformado (independientemente de finish_reason)
        # 2) finish_reason == "length" sin una respuesta final válida
        normalized_finish_reason = (
            finish_reason.strip().casefold()
            if isinstance(finish_reason, str)
            else ""
        )
        is_incomplete = has_unclosed_or_malformed_think or (
            normalized_finish_reason == "length" and not split.final_text.strip()
        )

        if is_incomplete:
            notice = "⚠️ La salida parece incompleta o malformada; no se mostrará como respuesta final."
            self._output.setPlainText(notice)
            # Preservar el razonamiento para inspección en la sección colapsable
            incomplete_reasoning = split.raw_output.replace("<think>", "").replace("</think>", "").strip()
            if incomplete_reasoning:
                self._reasoning_toggle.setVisible(True)
                self._reasoning_toggle.setChecked(False)
                self._reasoning_output.setPlainText(incomplete_reasoning)
                self._reasoning_output.setVisible(False)
            else:
                self._reasoning_toggle.setVisible(False)
                self._reasoning_output.setVisible(False)
                self._reasoning_output.clear()
        else:
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

        if self._pending_user_text is not None:
            if not is_incomplete and split.final_text.strip():
                self._history.append((self._pending_user_text, split.final_text))
            self._pending_user_text = None

        device_text = "No verificado" if dev == "unverified" else str(dev)
        reason_text = finish_reason if finish_reason else "no disponible"
        status = f"Completado. Motivo de finalización: {reason_text}. Dispositivo: {device_text}"
        self._status.setText(status)

    def _on_failed(self, msg):
        self._stop_think()
        self._think_indicator.setVisible(False)
        self._status.setText(msg)
        self._pending_user_text = None

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
        self._update_controls()

    def _tick_think(self):
        self._think_dots = (self._think_dots + 1) % 4
        dots = "." * self._think_dots
        self._think_indicator.setText(f"🧠 Think{dots}")

    def _stop_think(self):
        if self._think_timer.isActive():
            self._think_timer.stop()

    def _on_reasoning_toggled(self, checked):
        self._reasoning_output.setVisible(checked)

    def _update_controls(self):
        model_text = self._model.currentText().strip()
        device_text = self._device.currentText().strip()
        self._device_status.setText(f"Dispositivo solicitado: {device_text}")
        if self._models_loading:
            self._model_status.setText("Cargando artefactos OpenVINO locales…")
            self._run.setEnabled(False)
            return
        if self._models_error is not None:
            self._model_status.setText("No se pudo cargar la biblioteca local.")
            self._run.setEnabled(False)
            return
        if not self._inference_supported:
            self._model_status.setText(
                "Inferencia OpenVINO no disponible para la versión de Core confirmada."
            )
            self._run.setEnabled(False)
            return
        if not model_text or model_text == _EMPTY_MODELS_TEXT:
            self._model_status.setText(
                "Sin modelos OpenVINO locales. Actualiza la biblioteca local."
            )
            self._run.setEnabled(False)
            return
        if self._pending_model_id == model_text:
            self._model_status.setText(f"Modelo pendiente: {model_text}")
        else:
            self._model_status.setText(f"Modelo seleccionado: {model_text}")
        self._run.setEnabled(not self._service.is_busy)

    def _on_model_changed(self):
        model_id = self._model.currentText().strip()
        if model_id and model_id != _EMPTY_MODELS_TEXT:
            if model_id == self._pending_model_id:
                self._selected_format = (
                    self._pending_model_format
                    or self._model_formats.get(model_id)
                )
            else:
                self._selected_format = self._model_formats.get(model_id)
        self._update_controls()

    def set_loading(self) -> None:
        self._models_loading = True
        self._models_error = None
        self._status.setText("Cargando artefactos OpenVINO de la biblioteca local…")
        self._update_controls()

    def set_load_error(self, message: str) -> None:
        self.set_models([], error=message)

    def set_selected_model(self, model_id: str, format: Optional[str] = None) -> None:
        model_id = (model_id or "").strip()
        if not model_id:
            self._status.setText("Modelo OpenVINO no disponible.")
            return
        self._pending_model_id = model_id
        self._pending_model_format = format
        self._selected_format = format
        idx = self._model.findText(model_id)
        if idx >= 0:
            self._model.setCurrentIndex(idx)
            self._selected_format = format or self._model_formats.get(model_id)
            self._status.setText(f"Modelo seleccionado: {model_id}")
        else:
            self._status.setText(f"Modelo no disponible en OpenVINO: {model_id}")
        self._update_controls()

    def get_selected_format(self) -> Optional[str]:
        """Return the format of the currently selected model, if known."""
        return self._selected_format

    def set_inference_supported(self, supported: bool) -> None:
        """Set the version-gated integrated inference capability."""
        self._inference_supported = supported is True
        self._update_controls()

    def set_models(self, model_ids, formats=None, *, error=None, ambiguous_model_ids=()):
        previous_model_id = self._model.currentText().strip()
        self._model_formats = dict(formats or {})
        self._ambiguous_model_ids = set(ambiguous_model_ids or ())
        self._models_loading = False
        self._models_error = error
        self._model.clear()
        ids = [str(mid) for mid in (model_ids or []) if str(mid).strip()]
        if error is not None:
            self._model.addItem("Error al cargar modelos OpenVINO")
            item = self._model.model().item(0)
            if item is not None:
                item.setEnabled(False)
            self._model.setCurrentIndex(-1)
            self._status.setText(
                f"No se pudo cargar la biblioteca local: {error}"
            )
            self._update_controls()
            return
        if not ids:
            self._model.addItem(_EMPTY_MODELS_TEXT)
            item = self._model.model().item(0)
            if item is not None:
                item.setEnabled(False)
            self._model.setCurrentIndex(-1)
            if self._pending_model_id is not None:
                self._status.setText(
                    f"No se encontró '{self._pending_model_id}' entre los "
                    "artefactos OpenVINO locales."
                )
            else:
                self._status.setText(
                    "No hay artefactos OpenVINO locales. Actualiza la biblioteca local."
                )
            self._update_controls()
            return
        for mid in ids:
            self._model.addItem(mid)
        if self._pending_model_id in ids:
            selected_model_id = self._pending_model_id
            self._model.setCurrentIndex(ids.index(selected_model_id))
            self._selected_format = (
                self._model_formats.get(selected_model_id)
                or self._pending_model_format
            )
            self._pending_model_id = None
            self._pending_model_format = None
            self._status.setText(f"Modelo seleccionado: {selected_model_id}")
        else:
            selected_model_id = (
                previous_model_id if previous_model_id in ids else ids[0]
            )
            self._model.setCurrentIndex(ids.index(selected_model_id))
            if self._pending_model_id is not None:
                self._status.setText(
                    f"Modelo no disponible en OpenVINO: {self._pending_model_id}"
                )
            else:
                self._status.setText(f"Modelo seleccionado: {selected_model_id}")
        self._update_controls()
