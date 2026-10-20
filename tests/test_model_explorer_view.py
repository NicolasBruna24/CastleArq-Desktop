# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Model Explorer Qt view."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QListWidget

from castlearq_desktop.core.catalog_client import CatalogResult
from castlearq_desktop.core.catalog_controller import CatalogController
from castlearq_desktop.core.download_service import LocalArtifact, StoreState
from castlearq_desktop.core.model_catalog import ModelEntry
from castlearq_desktop.ui.model_explorer_view import ModelExplorerView


def _make_result(models: Optional[list] = None, error: Optional[str] = None) -> CatalogResult:
    return CatalogResult(
        models=tuple(models) if models is not None else None,
        error=error,
    )


def _entry(
    model_id: str = "qwen2.5-coder-7b-instruct",
    name: str = "Qwen2.5-Coder 7B Instruct",
    downloadable: bool = True,
) -> ModelEntry:
    return ModelEntry(
        model_id=model_id,
        name=name,
        provider="Qwen",
        family="Qwen2.5-Coder",
        task="coding",
        architecture="Transformer",
        parameter_count_b=7.0,
        context_length=32768,
        supported_runtimes=("llama.cpp / llama.app",),
        supported_backends=("Vulkan", "CPU"),
        quantizations=("Q4_K_M",),
        downloadable=downloadable,
    )


# --- Initial state: Core not ready ---


def test_initial_state_core_not_ready(qapp):
    controller = CatalogController(is_ready=lambda: False)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(False)
    assert view._button.isEnabled() is False
    assert "Core no disponible" in view._status.text()


# --- Loading and completion ---


def test_loading_and_completion(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)
    assert view._button.isEnabled() is True

    result = _make_result(models=[_entry()])
    controller.check_finished.emit(result)

    assert view._button.isEnabled() is True
    assert "1 modelo(s)" in view._status.text()
    assert view._list.count() == 1
    item = view._list.item(0)
    assert "Qwen2.5-Coder 7B Instruct" in item.text()


# --- Empty catalog ---


def test_empty_catalog(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    result = _make_result(models=[])
    controller.check_finished.emit(result)

    assert "No hay modelos disponibles" in view._status.text()
    assert view._list.count() == 0
    assert view._list.isEnabled() is False


# --- Error with no previous catalog ---


def test_error_no_previous_catalog(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    controller.check_failed.emit("No se puede conectar con CastleArq Core")

    assert "No se puede conectar" in view._status.text()
    assert view._list.count() == 0


# --- Error preserves previous catalog ---


def test_error_preserves_previous_catalog(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    # First: successful result
    result1 = _make_result(models=[_entry()])
    controller.check_finished.emit(result1)
    assert view._list.count() == 1

    # Then: error
    controller.check_failed.emit("Error HTTP al consultar modelos (código)")

    # Previous catalog preserved
    assert view._list.count() == 1
    assert "Error HTTP" in view._status.text()


# --- Button disabled during check ---


def test_button_disabled_during_check(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    controller.check_started.emit()
    assert view._button.isEnabled() is False

    controller.check_finished.emit(_make_result(models=[]))
    assert view._button.isEnabled() is True


# --- Core not ready disables button ---


def test_core_not_ready_disables_button(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)
    assert view._button.isEnabled() is True

    view.set_core_ready(False)
    assert view._button.isEnabled() is False
    assert "Core no disponible" in view._status.text()


# --- Model selection -------------------------------------------------


def test_model_selected_emits_signal(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    result = _make_result(models=[_entry()])
    controller.check_finished.emit(result)
    assert view._list.count() == 1

    received = []

    def _collect(entry):
        received.append(entry)

    view.model_selected.connect(_collect)

    item = view._list.item(0)
    view._on_item_clicked(item)

    assert len(received) == 1
    assert received[0].model_id == _entry().model_id


def test_selection_updates_details(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    result = _make_result(models=[_entry()])
    controller.check_finished.emit(result)

    item = view._list.item(0)
    view._on_item_clicked(item)

    text = view._detail.text()
    assert "Qwen2.5-Coder 7B Instruct" in text
    assert "Descargable: sí" in text


def test_selection_change_from_previous(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    first = _entry(model_id="model-a")
    second = _entry(model_id="model-b")
    controller.check_finished.emit(_make_result(models=[first, second]))

    # Select the first
    first_item = view._list.item(0)
    view._on_item_clicked(first_item)

    # Then select the second
    second_item = view._list.item(1)
    view._on_item_clicked(second_item)

    assert view.selected_entry is not None
    assert view.selected_entry.model_id == second.model_id


def test_empty_catalog_no_selection(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    result = _make_result(models=[])
    controller.check_finished.emit(result)

    assert view.selected_entry is None
    assert view._list.count() == 0


def test_downloadable_status_displayed(qapp):
    entry = _entry(downloadable=True)
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)

    result = _make_result(models=[entry])
    controller.check_finished.emit(result)

    item = view._list.item(0)
    assert "Descargable: sí" in item.text()


__all__ = []

# --- Download wiring ---


class FakeDownloader(QObject):
    """Minimal download-driver double with the service's signal surface."""

    started = Signal()
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.requests = []
        self.invalidated = False

    def request_download(self, request) -> bool:
        self.requests.append(request)
        return True

    def invalidate(self) -> None:
        self.invalidated = True


def _view_with_downloader(qapp, models=None):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    downloader = FakeDownloader()
    view.set_downloader(downloader)
    view._populate(tuple(models or [_entry()]))
    return view, downloader


def test_download_button_disabled_without_selection(qapp):
    view, _ = _view_with_downloader(qapp)
    assert view._download_button.isEnabled() is False


def test_download_button_enabled_for_downloadable_model(qapp):
    view, _ = _view_with_downloader(qapp)
    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    assert view._download_button.isEnabled() is True


def test_download_button_disabled_for_non_downloadable_model(qapp):
    view, _ = _view_with_downloader(qapp, [_entry(downloadable=False)])
    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    assert view._download_button.isEnabled() is False


def test_clicking_download_sends_the_selected_model(qapp):
    view, downloader = _view_with_downloader(qapp)
    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    view._on_download_clicked()

    assert len(downloader.requests) == 1
    assert downloader.requests[0].model_id == "qwen2.5-coder-7b-instruct"


def test_download_not_requested_without_a_downloader(qapp):
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view._populate((_entry(),))
    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    view._on_download_clicked()  # must not raise
    assert view._download_button.isEnabled() is False


def test_verified_result_is_reported_as_verified(qapp):
    from castlearq_desktop.core.download_service import (
        AcquisitionResult,
        AcquisitionStatus,
        StoreState,
    )

    view, _ = _view_with_downloader(qapp)
    view._on_download_finished(
        AcquisitionResult(
            status=AcquisitionStatus.READY,
            model_id="m",
            filename="m.gguf",
            store_state=StoreState.VERIFIED,
        )
    )
    assert "verificada" in view._status.text()
    assert "verificado por el Core" in view._detail.text()


def test_downloaded_is_not_presented_as_verified(qapp):
    from castlearq_desktop.core.download_service import (
        AcquisitionResult,
        AcquisitionStatus,
        StoreState,
    )

    view, _ = _view_with_downloader(qapp)
    view._on_download_finished(
        AcquisitionResult(
            status=AcquisitionStatus.READY,
            model_id="m",
            filename="m.gguf",
            store_state=StoreState.DOWNLOADED,
        )
    )
    assert "verificada" not in view._status.text()
    assert "descargado (según el Core)" in view._detail.text()


def test_unconfirmed_store_is_reported_as_unconfirmed(qapp):
    from castlearq_desktop.core.download_service import (
        AcquisitionResult,
        AcquisitionStatus,
    )

    view, _ = _view_with_downloader(qapp)
    view._on_download_finished(
        AcquisitionResult(status=AcquisitionStatus.READY, model_id="m")
    )
    assert "no lo confirma" in view._status.text()
    assert "no confirmado" in view._detail.text()


def test_invalidate_clears_the_download_state(qapp):
    view, downloader = _view_with_downloader(qapp)
    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    view.invalidate()

    assert downloader.invalidated is True
    assert view._download_result is None
    assert view._download_button.isEnabled() is False


def test_variant_choices_follow_the_selected_model(qapp):
    models = [
        _entry(model_id="a", name="A"),
        _entry(model_id="b", name="B"),
    ]
    models[0] = ModelEntry(**{**models[0].__dict__, "quantizations": ("Q4_K_M", "Q8_0")})
    view, _ = _view_with_downloader(qapp, models)

    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    assert view._variant_combo is not None
    assert view._variant_combo.count() == 2


def test_selected_quantization_is_propagated_to_the_request(qapp):
    from castlearq_desktop.core.model_catalog import ModelEntry

    entry = ModelEntry(
        model_id="qwen2.5-coder-7b-instruct",
        name="Qwen2.5-Coder 7B Instruct",
        provider="Qwen",
        family="Qwen2.5-Coder",
        task="coding",
        architecture="Transformer",
        parameter_count_b=7.0,
        context_length=32768,
        supported_runtimes=("llama.cpp / llama.app",),
        supported_backends=("Vulkan", "CPU"),
        quantizations=("Q4_K_M", "Q8_0"),
        downloadable=True,
    )
    view, downloader = _view_with_downloader(qapp, [entry])
    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))

    assert view._variant_combo is not None
    view._variant_combo.setCurrentText("Q8_0")
    view._on_download_clicked()

    assert downloader.requests[0].quantization == "Q8_0"


def test_request_carries_no_quantization_without_variants(qapp):
    view, downloader = _view_with_downloader(qapp)
    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    view._on_download_clicked()

    assert downloader.requests[0].quantization is None


def test_variants_of_a_previous_model_do_not_leak_into_the_next(qapp):
    models = [
        _entry(model_id="a", name="A"),
        _entry(model_id="b", name="B"),
    ]
    models[0] = ModelEntry(
        **{**models[0].__dict__, "quantizations": ("Q4_K_M", "Q8_0")}
    )
    view, downloader = _view_with_downloader(qapp, models)

    view._list.item(0).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(0))
    assert view._variant_combo is not None
    assert view._variant_combo.count() == 2

    view._list.item(1).setCheckState(Qt.Checked)
    view._on_item_clicked(view._list.item(1))
    # The second model declares a single quantization, so no combo remains.
    assert view._variant_combo is None
    view._on_download_clicked()
    assert downloader.requests[0].model_id == "b"
    assert downloader.requests[0].quantization is None


# --- Local availability (real artifacts reported by Core) ---------------------


def _view_with_local(models: list, artifacts: Optional[list]) -> ModelExplorerView:
    """A populated explorer, optionally fed with a correct library result."""
    controller = CatalogController(is_ready=lambda: True)
    view = ModelExplorerView(controller=controller, parent=None)
    view.set_core_ready(True)
    controller.check_finished.emit(_make_result(models=models))
    if artifacts is not None:
        view.set_local_artifacts(artifacts)
    return view


def _local_artifact(
    model_id: str = "qwen2.5-coder-7b-instruct",
    filename: str = "qwen-Q4_K_M.gguf",
    quantization: Optional[str] = "Q4_K_M",
    state: Optional[StoreState] = StoreState.VERIFIED,
    problem: Optional[str] = None,
) -> LocalArtifact:
    return LocalArtifact(
        model_id=model_id,
        filename=filename,
        quantization=quantization,
        state=state,
        problem=problem,
    )


def test_local_availability_is_not_claimed_without_a_correct_query(qapp):
    view = _view_with_local([_entry()], artifacts=None)
    view._on_item_clicked(view._list.item(0))

    # No library query yet: the section says nothing at all.
    assert view._local_detail.text() == ""


def test_local_availability_reports_a_model_without_local_artifacts(qapp):
    # A similar name in another model must not be mistaken for this model.
    other = _local_artifact(
        model_id="qwen2.5-coder-7b-instruct-clone",
        filename="qwen2.5-coder-7b-instruct-Q4_K_M.gguf",
    )
    view = _view_with_local([_entry()], artifacts=[other])
    view._on_item_clicked(view._list.item(0))

    text = view._local_detail.text()
    assert "Sin artefactos locales para este modelo" in text
    assert "qwen2.5-coder-7b-instruct-Q4_K_M.gguf" not in text


def test_local_availability_reports_one_verified_artifact(qapp):
    view = _view_with_local([_entry()], artifacts=[_local_artifact()])
    view._on_item_clicked(view._list.item(0))

    text = view._local_detail.text()
    assert "Disponibilidad local" in text
    assert "1 artefacto(s)" in text
    assert "qwen-Q4_K_M.gguf" in text
    assert "Q4_K_M" in text
    assert "verificado por el Core" in text


def test_local_availability_never_calls_downloaded_verified(qapp):
    view = _view_with_local(
        [_entry()], artifacts=[_local_artifact(state=StoreState.DOWNLOADED)]
    )
    view._on_item_clicked(view._list.item(0))

    text = view._local_detail.text()
    assert "descargado (según el Core)" in text
    assert "verificado por el Core" not in text
    assert "listo" not in text


def test_local_availability_shows_failed_state_and_problem(qapp):
    failed = _local_artifact(
        state=StoreState.FAILED, problem="File size differs from manifest"
    )
    view = _view_with_local([_entry()], artifacts=[failed])
    view._on_item_clicked(view._list.item(0))

    text = view._local_detail.text()
    assert "fallido (según el Core)" in text
    assert "File size differs from manifest" in text
    assert "verificado por el Core" not in text


def test_local_availability_lists_every_local_variant(qapp):
    artifacts = [
        _local_artifact(filename="q-Q4.gguf", quantization="Q4_K_M"),
        _local_artifact(
            filename="q-Q5.gguf",
            quantization="Q5_K_M",
            state=StoreState.DOWNLOADED,
        ),
    ]
    view = _view_with_local([_entry()], artifacts=artifacts)
    view._on_item_clicked(view._list.item(0))

    text = view._local_detail.text()
    assert "2 artefacto(s)" in text
    # Both variants are shown; none is picked arbitrarily.
    assert "q-Q4.gguf" in text and "Q4_K_M" in text
    assert "q-Q5.gguf" in text and "Q5_K_M" in text
    # Each variant keeps its own honest state.
    assert "verificado por el Core" in text
    assert "descargado (según el Core)" in text


def test_local_availability_reports_unknown_quantization(qapp):
    view = _view_with_local(
        [_entry()], artifacts=[_local_artifact(quantization=None)]
    )
    view._on_item_clicked(view._list.item(0))

    assert "cuantización no observada" in view._local_detail.text()


def test_local_availability_reports_unconfirmed_state(qapp):
    view = _view_with_local([_entry()], artifacts=[_local_artifact(state=None)])
    view._on_item_clicked(view._list.item(0))

    text = view._local_detail.text()
    assert "no confirmado" in text  # never an inferred success
    assert "verificado por el Core" not in text
    assert "descargado" not in text


def test_local_availability_shows_when_artifacts_arrive_before_the_selection(qapp):
    view = _view_with_local([_entry()], artifacts=[_local_artifact()])

    # The results arrive first: with no model selected nothing is shown.
    assert view._local_detail.text() == ""

    # Selecting the model afterwards reveals the already-received data.
    view._on_item_clicked(view._list.item(0))
    assert "qwen-Q4_K_M.gguf" in view._local_detail.text()


def test_local_availability_updates_when_the_local_artifacts_change(qapp):
    view = _view_with_local([_entry()], artifacts=[_local_artifact()])
    view._on_item_clicked(view._list.item(0))
    assert "verificado por el Core" in view._local_detail.text()

    # A new correct query reports the artifact as failed with a problem.
    view.set_local_artifacts(
        [_local_artifact(state=StoreState.FAILED, problem="bad magic")]
    )
    text = view._local_detail.text()
    assert "fallido (según el Core)" in text
    assert "bad magic" in text
    assert "verificado por el Core" not in text

    # Returning to "no correct query yet" claims nothing again.
    view.set_local_artifacts(None)
    assert view._local_detail.text() == ""
    # The catalog selection survived every update.
    assert view.selected_entry is not None
    assert view.selected_entry.model_id == _entry().model_id


def test_local_availability_follows_the_selected_model(qapp):
    models = [_entry(model_id="model-a"), _entry(model_id="model-b")]
    artifacts = [_local_artifact(model_id="model-a", filename="a.gguf")]
    view = _view_with_local(models, artifacts)

    view._on_item_clicked(view._list.item(0))
    assert "a.gguf" in view._local_detail.text()

    # Switching models re-derives the section for the new identity.
    view._on_item_clicked(view._list.item(1))
    assert "Sin artefactos locales para este modelo" in view._local_detail.text()
    assert "a.gguf" not in view._local_detail.text()

    # Deselecting clears the section entirely.
    view._on_item_clicked(view._list.item(1))
    assert view._local_detail.text() == ""
    assert view.selected_entry is None


def test_set_local_artifacts_does_not_replace_the_acquisition_detail(qapp):
    from castlearq_desktop.core.download_service import (
        AcquisitionResult,
        AcquisitionStatus,
    )

    view = _view_with_local([_entry()], artifacts=None)
    view._on_item_clicked(view._list.item(0))
    view._on_download_finished(
        AcquisitionResult(
            status=AcquisitionStatus.READY,
            model_id=_entry().model_id,
            filename="qwen-Q4_K_M.gguf",
        )
    )
    acquisition_detail = view._detail.text()
    assert "Estado en el almacén" in acquisition_detail

    # The library result must repaint only the separate local section.
    view.set_local_artifacts([_local_artifact()])

    assert view._detail.text() == acquisition_detail
    assert "Disponibilidad local" in view._local_detail.text()
    assert "qwen-Q4_K_M.gguf" in view._local_detail.text()
