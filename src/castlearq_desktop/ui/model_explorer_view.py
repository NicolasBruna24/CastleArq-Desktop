# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Model Explorer view: consults and presents the Core model catalog."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from castlearq_desktop.core.catalog_client import CatalogResult
from castlearq_desktop.core.catalog_controller import CatalogController


_TITLE = "Model Explorer"
_IDLE_TEXT = "Core no disponible"
_CHECKING_TEXT = "Consultando catálogo de modelos…"
_EMPTY_TEXT = "No hay modelos disponibles"
_ERROR_CONNECT = "No se puede conectar con CastleArq Core"
_ERROR_HTTP = "Error HTTP al consultar modelos (código)"
_ERROR_INVALID = "La respuesta del catálogo no tiene un formato válido"
_ERROR_STALE = "Se muestra el último catálogo válido; puede estar desactualizado."
_BUTTON_TEXT = "Actualizar catálogo"


class ModelExplorerView(QWidget):
    """Section that shows the Core model catalog."""

    def __init__(
        self,
        controller: CatalogController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._last_models: Optional[tuple] = None
        self._has_valid_catalog = False

        box = QGroupBox(_TITLE, self)
        layout = QVBoxLayout(box)

        self._status = QLabel(_IDLE_TEXT, box)
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._status)

        self._list = QListWidget(box)
        self._list.setEnabled(False)
        layout.addWidget(self._list)

        self._detail = QLabel("", box)
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)

        self._button = QPushButton(_BUTTON_TEXT, box)
        self._button.setEnabled(False)
        self._button.clicked.connect(self._on_check)
        layout.addWidget(self._button)

        outer = QVBoxLayout(self)
        outer.addWidget(box)

        controller.check_started.connect(self._on_started)
        controller.already_running.connect(self._on_already)
        controller.check_finished.connect(self._on_finished)
        controller.check_failed.connect(self._on_failed)

    def _on_check(self) -> None:
        self._controller.check()

    def _on_started(self) -> None:
        self._button.setEnabled(False)
        self._status.setText(_CHECKING_TEXT)
        self._detail.setText("")

    def _on_already(self) -> None:
        pass  # button already disabled during check

    def _on_finished(self, result: CatalogResult) -> None:
        self._button.setEnabled(True)
        self._detail.setText("")
        models = result.models or ()
        self._last_models = models
        self._has_valid_catalog = True
        self._populate(models)
        if not models:
            self._status.setText(_EMPTY_TEXT)
        else:
            self._status.setText(f"{len(models)} modelo(s) disponible(s)")

    def _on_failed(self, message: str) -> None:
        self._button.setEnabled(True)
        self._detail.setText("")
        if self._has_valid_catalog:
            self._status.setText(f"{message} — {_ERROR_STALE}")
        else:
            self._status.setText(message)

    def _populate(self, models: tuple) -> None:
        self._list.clear()
        self._list.setEnabled(bool(models))
        for entry in models:
            item = QListWidgetItem()
            lines = [entry.name]
            if entry.provider:
                lines.append(f"  Proveedor: {entry.provider}")
            if entry.family:
                lines.append(f"  Familia: {entry.family}")
            if entry.task:
                lines.append(f"  Tarea: {entry.task}")
            if entry.architecture:
                lines.append(f"  Arquitectura: {entry.architecture}")
            if entry.parameter_count_b is not None:
                lines.append(f"  Parámetros: {entry.parameter_count_b} B")
            if entry.context_length is not None:
                lines.append(f"  Contexto: {entry.context_length}")
            if entry.quantizations:
                lines.append(f"  Cuantizaciones: {', '.join(entry.quantizations)}")
            if entry.downloadable:
                lines.append("  Descargable: sí")
            else:
                lines.append("  Descargable: no")
            item.setText("\n".join(lines))
            self._list.addItem(item)

    def set_core_ready(self, ready: bool) -> None:
        """Called by MainWindow when Core readiness changes."""
        if ready:
            self._button.setEnabled(not self._controller.is_checking)
            if not self._has_valid_catalog:
                self._status.setText(_IDLE_TEXT)
        else:
            self._button.setEnabled(False)
            self._status.setText(_IDLE_TEXT)

    def invalidate(self) -> None:
        """Called when Core shuts down: discard active generation."""
        self._controller.invalidate()


__all__ = ["ModelExplorerView"]