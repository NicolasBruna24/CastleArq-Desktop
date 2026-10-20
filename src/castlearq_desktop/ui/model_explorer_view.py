# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Model Explorer view: consults and presents the Core model catalog."""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from castlearq_desktop.core.catalog_client import CatalogResult
from castlearq_desktop.core.catalog_controller import CatalogController
from castlearq_desktop.core.download_service import (
    AcquisitionResult,
    AcquisitionStatus,
    LocalArtifact,
    ModelAcquisitionService,
    Request,
    StoreState,
)
from castlearq_desktop.core.model_catalog import ModelEntry


_TITLE = "Model Explorer"
_IDLE_TEXT = "Core no disponible"
_CHECKING_TEXT = "Consultando catálogo de modelos…"
_EMPTY_TEXT = "No hay modelos disponibles"
_ERROR_CONNECT = "No se puede conectar con CastleArq Core"
_ERROR_HTTP = "Error HTTP al consultar modelos (código)"
_DOWNLOAD_TEXT = "Descargar"
_ERROR_STALE = "Se muestra el último catálogo válido; puede estar desactualizado."
_BUTTON_TEXT = "Actualizar catálogo"


def _store_state_text(state: Optional[StoreState]) -> str:
    """Describe Core's own store state, never upgrading it on Desktop's word."""
    if state is StoreState.VERIFIED:
        return "verificado por el Core"
    if state is StoreState.DOWNLOADED:
        return "descargado (según el Core)"
    if state is StoreState.DOWNLOADING:
        return "descargando (según el Core)"
    if state is StoreState.FAILED:
        return "fallido (según el Core)"
    if state is StoreState.NOT_DOWNLOADED:
        return "no descargado (según el Core)"
    return "no confirmado por el almacén local"


_LOCAL_AVAILABILITY_TITLE = "Disponibilidad local"


def local_availability_lines(
    artifacts: Optional[tuple],
    model_id: str,
) -> list[str]:
    """Describe the local artifacts Core reports for one catalog ``model_id``.

    ``None`` means "no correct library query yet": nothing is claimed at all.
    An empty result of the match means the store really lists no artifact for
    this exact ``model_id``. Presence, quantization, state and problem come
    exclusively from Core's own ``list --json`` entries as read by the local
    library — never from ``downloadable``, repositories nor name similarity —
    and the state wording is the shared ``store_state_text`` vocabulary, so a
    ``downloaded`` artifact is never presented as verified or ready.
    """
    if artifacts is None:
        return []
    matches = [a for a in artifacts if a.model_id == model_id]
    if not matches:
        return [
            f"{_LOCAL_AVAILABILITY_TITLE}:",
            "  Sin artefactos locales para este modelo",
        ]
    lines = [
        f"{_LOCAL_AVAILABILITY_TITLE}: {len(matches)} artefacto(s)"
        " reportado(s) por el Core"
    ]
    for artifact in matches:
        quantization = artifact.quantization or "cuantización no observada"
        lines.append(
            f"  {artifact.filename} — Cuantización: {quantization}"
            f" — Estado: {_store_state_text(artifact.state)}"
        )
        if artifact.problem:
            lines.append(f"    Problema (Core): {artifact.problem}")
    return lines


class ModelExplorerView(QWidget):
    """Section that shows the Core model catalog."""

    #: Emitted when an acquisition ends, so the local library can be
    #: refreshed afterwards. The catalog state stays independent from it.
    acquisition_finished = Signal(object)  # AcquisitionResult

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
        self._box = box
        self._box_layout = layout

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

        # Separate, always-derived section: the real artifacts Core lists for
        # the selected model. Visually and logically apart from _detail, which
        # also carries the state of the last acquisition after a download.
        self._local_detail = QLabel("", box)
        self._local_detail.setWordWrap(True)
        self._local_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._local_detail)

        self._button = QPushButton(_BUTTON_TEXT, box)
        self._button.setEnabled(False)
        self._button.clicked.connect(self._on_check)
        layout.addWidget(self._button)

        self._download_button = QPushButton(_DOWNLOAD_TEXT, box)
        self._download_button.setEnabled(False)
        self._download_button.clicked.connect(self._on_download_clicked)
        layout.addWidget(self._download_button)

        self._variant_combo: Optional[QComboBox] = None
        self._download_request: Optional[Request] = None
        self._download_result: Optional[AcquisitionResult] = None
        self._downloader: Optional[ModelAcquisitionService] = None
        self._downloading = False
        self._updating_selection = False
        #: ``None`` until a *correct* library query delivers its full result;
        #: an empty tuple means "Core lists no artifacts". While ``None``
        #: nothing is claimed about local availability.
        self._local_artifacts: Optional[tuple] = None

        outer = QVBoxLayout(self)
        outer.addWidget(box)

        controller.check_started.connect(self._on_started)
        controller.already_running.connect(self._on_already)
        controller.check_finished.connect(self._on_finished)
        controller.check_failed.connect(self._on_failed)
        self._list.itemChanged.connect(self._on_item_clicked)

    # -- Download (one-shot Core CLI acquisition) ---------------------------
    def set_downloader(self, downloader: Optional[ModelAcquisitionService]) -> None:
        """Attach a download driver and wire its signals into this view."""
        if self._downloader is not None:
            self._downloader.started.disconnect(self._on_download_started)
            self._downloader.progress.disconnect(self._on_download_progress)
            self._downloader.finished.disconnect(self._on_download_finished)
            self._downloader.failed.disconnect(self._on_download_failed)
            self._downloader.error.disconnect(self._on_download_error)
        self._downloader = downloader
        if downloader is not None:
            downloader.started.connect(self._on_download_started)
            downloader.progress.connect(self._on_download_progress)
            downloader.finished.connect(self._on_download_finished)
            downloader.failed.connect(self._on_download_failed)
            downloader.error.connect(self._on_download_error)

    def _on_download_started(self) -> None:
        self._downloading = True
        self._status.setText("Descargando desde CastleArq Core…")

    def _on_download_progress(self, line: str) -> None:
        self._status.setText(line if line else self._status.text())

    def _on_download_finished(self, result: AcquisitionResult) -> None:
        self._download_result = result
        self._downloading = False
        self.acquisition_finished.emit(result)
        lines = [f"  Modelo: {result.model_id}"]
        if result.filename:
            lines.append(f"  Variante: {result.filename}")
        if result.destination:
            lines.append(f"  Destino: {result.destination}")
        if result.manifest_path:
            lines.append(f"  Registro local: {result.manifest_path}")
        lines.append(f"  Estado en el almacén: {_store_state_text(result.store_state)}")
        if result.message:
            lines.append(result.message)
        self._detail.setText("\n".join(lines) if lines else "")
        self._update_download_button()
        if result.status == AcquisitionStatus.ALREADY_DOWNLOADED:
            self._status.setText("El modelo ya estaba descargado (informe del Core).")
        elif result.store_verified:
            self._status.setText("Descarga completada y verificada por el Core.")
        elif result.store_confirmed:
            self._status.setText("Descarga completada (el Core lo registra como descargado).")
        else:
            self._status.setText(
                "Descarga completada según el Core; el almacén local no lo confirma."
            )

    def _on_download_failed(self, message: str) -> None:
        self._downloading = False
        self._status.setText(f"Descarga fallida: {message}")
        self._update_download_button()

    def _on_download_error(self, message: str) -> None:
        self._downloading = False
        self._status.setText(f"Error de descarga: {message}")
        self._update_download_button()

    def _update_download_button(self) -> None:
        if self._downloader is None:
            self._download_button.setEnabled(False)
            return
        entry = self.selected_entry
        if entry is None or not entry.downloadable or self._downloading:
            self._download_button.setEnabled(False)
            return
        self._download_button.setEnabled(True)

    def _on_download_clicked(self) -> None:
        if self._downloader is None:
            return
        entry = self.selected_entry
        if entry is None or not entry.downloadable or self._downloading:
            return
        quantization = None
        if self._variant_combo is not None and self._variant_combo.count() > 0:
            quantization = self._variant_combo.currentText() or None
        self._download_request = Request(
            model_id=entry.model_id,
            quantization=quantization,
            filename=None,
        )
        self._download_result = None
        self._download_button.setEnabled(False)
        ok = self._downloader.request_download(self._download_request)
        if not ok:
            self._download_button.setEnabled(True)

    # -- Model selection (explicit user-driven selection of a catalog model) --
    model_selected = Signal(ModelEntry)  # model selected by the user
    model_unselected = Signal()  # no model is selected any more

    def _on_check(self) -> None:
        self._controller.check()

    def _on_started(self) -> None:
        self._button.setEnabled(False)
        self._status.setText(_CHECKING_TEXT)
        self._detail.setText("")

    def _on_already(self) -> None:
        pass  # button already disabled during check

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.UserRole)
        if entry is None:
            return
        if self._updating_selection:
            # The state change that triggered this handler is our own; acting
            # on it again would toggle the selection back and recurse.
            return
        if item.checkState() == Qt.Checked:
            # Deselect this model
            self._set_check_state(item, Qt.Unchecked)
            self.model_unselected.emit()
            self._update_detail(None)
            self._refresh_variant_choices(None)
            self._update_download_button()
        else:
            # Select this model: uncheck all others, check this one
            self._updating_selection = True
            try:
                for i in range(self._list.count()):
                    other = self._list.item(i)
                    if other is not item and other.checkState() == Qt.Checked:
                        other.setCheckState(Qt.Unchecked)
                item.setCheckState(Qt.Checked)
            finally:
                self._updating_selection = False
            self.model_selected.emit(entry)
            self._update_detail(entry)
            self._refresh_variant_choices(entry)
            self._update_download_button()

    def _set_check_state(self, item: QListWidgetItem, state: Qt.CheckState) -> None:
        """Change one item's check state without re-entering the handler."""
        self._updating_selection = True
        try:
            item.setCheckState(state)
        finally:
            self._updating_selection = False

    def _clear_selection(self) -> None:
        had_selection = any(
            self._list.item(i).checkState() == Qt.Checked
            for i in range(self._list.count())
        )
        self._updating_selection = True
        try:
            for i in range(self._list.count()):
                item = self._list.item(i)
                item.setCheckState(Qt.Unchecked)
        finally:
            self._updating_selection = False
        if had_selection:
            self.model_unselected.emit()
        # With no model selected there is nothing to report locally.
        self._refresh_local_availability()

    def _update_detail(self, entry: Optional[ModelEntry]) -> None:
        if entry is None:
            self._detail.setText("")
            self._refresh_local_availability()
            return
        lines = [entry.name]
        if entry.model_id:
            lines.append(f"  ID modelo: {entry.model_id}")
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
        if entry.supported_runtimes:
            lines.append(f"  Runtime: {', '.join(entry.supported_runtimes)}")
        if entry.supported_backends:
            lines.append(f"  Backend: {', '.join(entry.supported_backends)}")
        if entry.quantizations:
            lines.append(f"  Cuantizaciones: {', '.join(entry.quantizations)}")
        lines.append(f"  Descargable: {'sí' if entry.downloadable else 'no'}")
        if entry.download_source:
            lines.append(f"  Origen: {entry.download_source}")
        if entry.download_repository:
            lines.append(f"  Repositorio: {entry.download_repository}")
        self._detail.setText("\n".join(lines))
        self._refresh_local_availability()

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
        self._update_download_button()

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
        self._clear_selection()
        self._refresh_variant_choices(None)
        for entry in models:
            item = QListWidgetItem()
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
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
            if entry.supported_runtimes:
                lines.append(f"  Runtime: {', '.join(entry.supported_runtimes)}")
            if entry.supported_backends:
                lines.append(f"  Backend: {', '.join(entry.supported_backends)}")
            if entry.quantizations:
                lines.append(f"  Cuantizaciones: {', '.join(entry.quantizations)}")
            lines.append(f"  Descargable: {'sí' if entry.downloadable else 'no'}")
            item.setText("\n".join(lines))
            item.setData(Qt.UserRole, entry)  # entry tied to the item
            self._list.addItem(item)

    def _refresh_variant_choices(self, entry: Optional[ModelEntry]) -> None:
        """Show the quantizations of the selected model, and only those.

        The combo is created on demand and removed again when it is not
        needed, so it never lingers in the layout with stale choices.
        """
        choices = tuple(entry.quantizations) if entry is not None else ()
        if len(choices) < 2:
            if self._variant_combo is not None:
                self._box_layout.removeWidget(self._variant_combo)
                self._variant_combo.deleteLater()
                self._variant_combo = None
            return
        if self._variant_combo is None:
            self._variant_combo = QComboBox()
            self._box_layout.insertWidget(1, self._variant_combo)
        self._variant_combo.blockSignals(True)
        try:
            self._variant_combo.clear()
            for quantization in choices:
                self._variant_combo.addItem(quantization)
        finally:
            self._variant_combo.blockSignals(False)

    # -- local availability (fed by the real library query) -----------------

    def set_local_artifacts(
        self, artifacts: Optional[Iterable[LocalArtifact]]
    ) -> None:
        """Receive the complete artifact list of a correct library query.

        A stable copy is stored and only the "Disponibilidad local" section
        is repainted: the catalog selection, the library selection and the
        acquisition state currently shown in the detail panel are untouched.
        Works whether the artifacts arrive before or after a model is
        selected. ``None`` restores "no correct query yet", which claims
        nothing about local availability.
        """
        self._local_artifacts = None if artifacts is None else tuple(artifacts)
        self._refresh_local_availability()

    def _refresh_local_availability(self) -> None:
        """Repaint the local section for the currently selected model."""
        entry = self.selected_entry
        if entry is None:
            self._local_detail.setText("")
            return
        lines = local_availability_lines(self._local_artifacts, entry.model_id)
        self._local_detail.setText("\n".join(lines))

    def set_core_ready(self, ready: bool) -> None:
        """Called by MainWindow when Core readiness changes."""
        if ready:
            self._button.setEnabled(not self._controller.is_checking)
            if not self._has_valid_catalog:
                self._status.setText(_IDLE_TEXT)
        else:
            self._button.setEnabled(False)
            self._status.setText(_IDLE_TEXT)
            self._clear_selection()
            self._detail.setText("")
        self._update_download_button()

    def invalidate(self) -> None:
        """Called when Core shuts down: discard active generation and pending download."""
        self._controller.invalidate()
        if self._downloader is not None:
            self._downloader.invalidate()
        self._downloading = False
        self._download_request = None
        self._clear_selection()
        self._download_result = None
        self._update_download_button()

    # ------------------------------------------------------------------ #
    # Selection access from MainWindow / tests                            #
    # ------------------------------------------------------------------ #

    @property
    def selected_entry(self) -> Optional[ModelEntry]:
        """The most recently checked model, or None if nothing is selected."""
        selected: Optional[ModelEntry] = None
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                selected = item.data(Qt.UserRole)
        return selected

    @property
    def selected_model(self) -> Optional[ModelEntry]:
        return self.selected_entry

    def clear_selection(self) -> None:
        """Clear the current selection so UI stays in sync."""
        self._clear_selection()

    def update_detail(self, entry: Optional[ModelEntry]) -> None:
        """Refresh the detail line for the currently selected model."""
        if entry is None:
            self._detail.setText("")
            return
        self._update_detail(entry)


__all__ = ["ModelExplorerView"]