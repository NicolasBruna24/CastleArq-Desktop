# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Local library view: shows the artifacts Core found in the local store.

The view is deliberately **not** a second catalog. It asks Core's public CLI
(``castlearq list --json``) which artifacts exist in the real storage and
presents exactly those, with the state Core itself reports. A model that is
downloadable but absent from the store never appears here, and an artifact
that is present but not verified is presented as such.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QSizePolicy,
    QWidget,
)

from castlearq_desktop.core.download_service import (
    LocalArtifact,
    LocalLibraryService,
    StoreInfo,
    StoreState,
)
from castlearq_desktop.ui.styles import make_font

_TITLE = "Biblioteca local"
_IDLE_TEXT = "Sin consultar"
_CHECKING_TEXT = "Consultando la biblioteca local…"
_EMPTY_TEXT = "La biblioteca local está vacía: no hay artefactos en el almacén"
_REFRESH_TEXT = "Actualizar biblioteca"
_UNVERIFIED_TEXT = "Este artefacto no está verificado por el Core."


def store_state_text(state: Optional[StoreState]) -> str:
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
    return "estado no informado por el Core"


def store_state_lines(store: Optional[StoreInfo]) -> str:
    """Describe the store Core itself selected, or say that it is unknown."""
    if store is None or not store.path:
        return "Almacén local: no informado por el Core"
    source = f" (origen: {store.source})" if store.source else ""
    return f"Almacén local: {store.path}{source}"


def artifact_lines(artifact: LocalArtifact) -> list[str]:
    """Render one artifact as the lines shown in the list."""
    lines = [artifact.filename]
    lines.append(f"  Modelo: {artifact.model_id}")
    lines.append(
        f"  Cuantización: {artifact.quantization or 'no observada por el Core'}"
    )
    lines.append(f"  Estado: {store_state_text(artifact.state)}")
    if artifact.size_bytes is not None:
        lines.append(f"  Tamaño: {artifact.size_bytes} bytes")
    return lines


def _find_artifact(
    artifacts: list,
    model_id: str,
    filename: str,
    quantization: Optional[str] = None,
) -> Optional[LocalArtifact]:
    """Locate an artifact by its logical identity (model_id + filename).

    The quantization is used only to disambiguate when several entries share
    the same identity; identity never upgrades the state Core reported.
    """
    matches = [
        a
        for a in artifacts
        if a.model_id == model_id and a.filename == filename
    ]
    if not matches:
        return None
    if quantization is not None:
        refined = [a for a in matches if a.quantization == quantization]
        if refined:
            return refined[0]
    return matches[0]


def _same_identity(a: LocalArtifact, b: LocalArtifact) -> bool:
    """Whether two artifacts denote the same logical library entry."""
    return (
        a.model_id == b.model_id
        and a.filename == b.filename
        and (a.quantization or None) == (b.quantization or None)
    )



class LocalLibraryView(QWidget):
    """Section that lists the artifacts installed in the local model store."""

    artifact_selected = Signal(object)  # LocalArtifact
    artifact_unselected = Signal()
    open_chat_requested = Signal(object)  # LocalArtifact

    def __init__(
        self,
        service: LocalLibraryService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._artifacts: list[LocalArtifact] = []
        self._selected: Optional[LocalArtifact] = None
        self._store: Optional[StoreInfo] = None
        #: One-shot request to select an artifact in the next real results
        #: (set after a finished acquisition, consumed by exactly one query).
        self._pending_selection: Optional[tuple[str, str, Optional[str]]] = None
        #: True while the list is being rebuilt: transient current-item
        #: changes produced by clear() are not real selection changes.
        self._repopulating = False
        #: Lazy check wired by MainWindow to ``ChatView.has_session``: asked
        #: right before applying a pending selection, because a chat session
        #: may open while the query runs. This view never owns session state.
        self._session_active_provider: Optional[Callable[[], bool]] = None

        box = QGroupBox(_TITLE, self)
        layout = QVBoxLayout(box)
        self._box = box
        self._box_layout = layout

        self._status = QLabel(_IDLE_TEXT, box)
        self._status.setWordWrap(True)
        self._status.setFont(make_font(12))
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._status)

        self._store_label = QLabel("", box)
        self._store_label.setWordWrap(True)
        self._store_label.setFont(make_font(12))
        self._store_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._store_label)

        self._list = QListWidget(box)
        self._list.setEnabled(False)
        self._list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self._list)

        self._detail = QLabel("", box)
        self._detail.setWordWrap(True)
        self._detail.setFont(make_font(12))
        layout.addWidget(self._detail)

        self._button = QPushButton(_REFRESH_TEXT, box)
        self._button.clicked.connect(self._on_refresh)
        layout.addWidget(self._button)

        self._open_chat_button = QPushButton("Abrir chat", box)
        self._open_chat_button.setEnabled(False)
        self._open_chat_button.clicked.connect(self._on_open_chat)
        layout.addWidget(self._open_chat_button)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(box)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._service.started.connect(self._on_started)
        self._service.finished.connect(self._on_finished)
        self._service.failed.connect(self._on_failed)

    # -- actions ------------------------------------------------------------

    def _on_refresh(self) -> None:
        """Ask Core for the real contents of the local store."""
        self._service.refresh()

    def _on_started(self) -> None:
        self._button.setEnabled(False)
        self._status.setText(_CHECKING_TEXT)
        self._detail.setText("")

    def _on_finished(self, artifacts: list, store: Any = None) -> None:
        self._button.setEnabled(True)
        self._artifacts = list(artifacts)
        self._store = store if isinstance(store, StoreInfo) else None
        self._populate(self._artifacts)
        if not self._artifacts:
            self._status.setText(_EMPTY_TEXT)
        else:
            self._status.setText(
                f"{len(self._artifacts)} artefacto(s) instalado(s) en el almacén local"
            )
        self._store_label.setText(store_state_lines(self._store))

    def _on_failed(self, message: str) -> None:
        self._button.setEnabled(True)
        # A failed query delivered no results: the pending selection cannot
        # be applied anymore and must never leak into an unrelated query.
        self._pending_selection = None
        self._detail.setText("")
        self._status.setText(message)

    def _populate(self, artifacts: list) -> None:
        """Rebuild the list, keeping the logical selection stable.

        The previous selection (and the one-shot pending selection, if any)
        is resolved against the new results *before* touching the list, so
        the rebuild never looks like a deselection: no ``artifact_*`` signal
        is emitted while the logical selection does not change, and exactly
        one real signal is published afterwards when it does.
        """
        previous = self._selected
        pending = self._pending_selection
        self._pending_selection = None  # consumed exactly once, on real results

        target: Optional[LocalArtifact] = None
        applied_pending = False
        if pending is not None and self._session_active():
            # A conversation was opened while this query was running: its
            # artifact must never be replaced by the downloaded one. The
            # pending request was already consumed above, so it can neither
            # interrupt this session nor apply to a later, unrelated refresh.
            pending = None
        if pending is not None:
            target = _find_artifact(artifacts, *pending)
            applied_pending = target is not None
        if target is None and previous is not None:
            target = _find_artifact(
                artifacts,
                previous.model_id,
                previous.filename,
                previous.quantization,
            )

        self._repopulating = True
        try:
            self._list.clear()
            self._list.setEnabled(bool(artifacts))
            for artifact in artifacts:
                item = QListWidgetItem()
                item.setText("\n".join(artifact_lines(artifact)))
                item.setData(Qt.UserRole, artifact)
                self._list.addItem(item)
            if target is not None:
                for row in range(self._list.count()):
                    item = self._list.item(row)
                    if item.data(Qt.UserRole) is target:
                        # Restores the selection without passing through
                        # "none" and keeps the artifact visible.
                        self._list.setCurrentItem(item)
                        self._list.scrollToItem(item)
                        break
                self._selected = target
                # The detail always shows the state of the *fresh* entry.
                self._detail.setText("\n".join(self._detail_lines(target)))
            else:
                self._selected = None
                self._detail.setText("")
        finally:
            self._repopulating = False

        # Publish one —and only one— signal for a real selection change.
        if applied_pending:
            if previous is None or not _same_identity(previous, target):
                # An acquisition asked for this artifact: normal selection
                # signals update MainWindow and the chat section.
                self.artifact_selected.emit(target)
        elif previous is not None and target is None:
            # The selected artifact no longer exists in Core's results.
            self.artifact_unselected.emit()


    # -- selection ----------------------------------------------------------

    def _on_open_chat(self) -> None:
        if self._selected is None:
            return
        self.open_chat_requested.emit(self._selected)


    def _on_current_changed(
        self, current: Optional[QListWidgetItem], _previous: Any
    ) -> None:
        if self._repopulating:
            # During a rebuild, Qt's transient current-item changes (clear()
            # moving the cursor through remaining items) are not real user
            # selection changes: _populate decides and reports the outcome.
            return
        artifact = current.data(Qt.UserRole) if current is not None else None
        if not isinstance(artifact, LocalArtifact):
            self._selected = None
            self._detail.setText("")
            self._open_chat_button.setEnabled(False)
            self.artifact_unselected.emit()
            return
        self._selected = artifact
        self._detail.setText("\n".join(self._detail_lines(artifact)))
        self._open_chat_button.setEnabled(True)
        self.artifact_selected.emit(artifact)

    def _detail_lines(self, artifact: LocalArtifact) -> list[str]:
        lines = [f"Artefacto: {artifact.filename}"]
        lines.append(f"  Modelo: {artifact.model_id}")
        lines.append(
            f"  Cuantización: {artifact.quantization or 'no observada por el Core'}"
        )
        lines.append(f"  Estado: {store_state_text(artifact.state)}")
        if artifact.size_bytes is not None:
            lines.append(f"  Tamaño: {artifact.size_bytes} bytes")
        if artifact.problem:
            lines.append(f"  Problema (Core): {artifact.problem}")
        if not artifact.is_verified:
            lines.append(f"  {_UNVERIFIED_TEXT}")
        return lines

    # -- pending selection --------------------------------------------------

    def set_pending_selection(
        self,
        model_id: str,
        filename: str,
        quantization: Optional[str] = None,
    ) -> None:
        """Request the selection of one artifact in the next real results.

        Used after a finished acquisition. The request is applied only if
        Core's own results actually contain the artifact, it is consumed
        exactly once (a later, unrelated query never re-applies it), and it
        never upgrades the state Core reported for that artifact.
        """
        if not model_id or not filename:
            # Identity is incomplete: no selection can be requested safely.
            self._pending_selection = None
            return
        self._pending_selection = (model_id, filename, quantization)

    def clear_pending_selection(self) -> None:
        """Drop any pending selection (failed acquisition / shutdown)."""
        self._pending_selection = None

    def set_session_active_provider(
        self, provider: Optional[Callable[[], bool]]
    ) -> None:
        """Register the lazy session check used before applying a pending selection.

        Wired by MainWindow to ``ChatView.has_session`` so the check reads the
        single source of truth at consumption time without this view importing
        the chat module or duplicating any session state.
        """
        self._session_active_provider = provider

    def _session_active(self) -> bool:
        """Whether a chat session is open right now (asked lazily).

        With no provider registered the answer is ``False``. A provider that
        raises is treated as "session active": an unknown state must never
        allow a pending selection to interrupt a real conversation.
        """
        provider = self._session_active_provider
        if provider is None:
            return False
        try:
            return bool(provider())
        except Exception:  # noqa: BLE001 - a broken check must not break the refresh
            return True

    # -- external access ----------------------------------------------------

    @property
    def artifacts(self) -> tuple:
        """The artifacts Core reported in the last successful query."""
        return tuple(self._artifacts)

    @property
    def selected_artifact(self) -> Optional[LocalArtifact]:
        """The artifact currently selected by the user, if any."""
        return self._selected

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the explicit refresh action."""
        self._button.setEnabled(enabled)

    def invalidate(self) -> None:
        """Drop pending results (window close / Core shutdown)."""
        self._service.invalidate()
        self._pending_selection = None
        self._artifacts = []
        self._selected = None
        self._list.clear()
        self._list.setEnabled(False)
        self._detail.setText("")
        self._status.setText(_IDLE_TEXT)
        self._store_label.setText("")
        self._store = None
        self._button.setEnabled(True)


__all__ = [
    "LocalLibraryView",
    "artifact_lines",
    "store_state_lines",
    "store_state_text",
]
