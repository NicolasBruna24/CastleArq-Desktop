# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Model Explorer Qt view."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget

from castlearq_desktop.core.catalog_client import CatalogResult
from castlearq_desktop.core.catalog_controller import CatalogController
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


__all__ = []