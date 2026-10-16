# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Tests for the catalog controller and concurrency behavior."""

from __future__ import annotations

import threading
import time
from typing import Optional

import pytest

from PySide6.QtCore import QThread, QThreadPool

from castlearq_desktop.core.catalog_client import CatalogResult
from castlearq_desktop.core.catalog_controller import CatalogController
from castlearq_desktop.core.model_catalog import ModelEntry


class _FakeFetch:
    def __init__(self, body: bytes, delay: float = 0.0, exc: Optional[Exception] = None):
        self._body = body
        self._delay = delay
        self._exc = exc
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, *, deadline: Optional[float] = None) -> bytes:
        with self.lock:
            self.calls += 1
        if self._exc is not None:
            raise self._exc
        if self._delay > 0:
            time.sleep(self._delay)
        return self._body


def _make_result_json(models: list[dict]) -> bytes:
    import json
    return json.dumps({"models": models}).encode("utf-8")


def test_check_starts_only_when_ready(qapp):
    calls = []
    fetch = _FakeFetch(_make_result_json([]))
    ready = [True]

    def is_ready():
        calls.append(1)
        return ready[0]

    ctl = CatalogController(is_ready=is_ready, fetch=fetch)
    assert ctl.check() is True
    assert len(calls) == 1
    # Simulate async completion: deliver the result directly to the
    # coordinator, exercising the same generation-based invalidation logic
    # as the Qt transport path.
    result = CatalogResult(models=())
    ctl._on_result(ctl._generation, result)
    assert not ctl.is_checking
    assert fetch.calls == 0


def test_check_rejected_when_not_ready(qapp):
    fetch = _FakeFetch(_make_result_json([]))
    ready = [False]

    def is_ready():
        return ready[0]

    ctl = CatalogController(is_ready=is_ready, fetch=fetch)
    assert ctl.check() is False
    assert fetch.calls == 0


def test_no_concurrent_checks(qapp):
    fetch = _FakeFetch(_make_result_json([]), delay=0.5)
    ready = [True]

    def is_ready():
        return ready[0]

    ctl = CatalogController(is_ready=is_ready, fetch=fetch)
    assert ctl.check() is True
    assert ctl.check() is False  # second trigger rejected
    # Simulate async completion: deliver the result directly to the
    # coordinator.
    result = CatalogResult(models=())
    ctl._on_result(ctl._generation, result)


def test_stale_result_discarded_after_invalidation(qapp):
    fetch = _FakeFetch(_make_result_json([]), delay=0.3)
    ready = [True]

    def is_ready():
        return ready[0]

    ctl = CatalogController(is_ready=is_ready, fetch=fetch)
    assert ctl.check() is True
    ctl.invalidate()  # retire generation before result arrives
    # Simulate delivery of a stale result with a superseded generation.
    # The coordinator must discard it because the generation no longer
    # matches the active attempt.
    result = CatalogResult(models=())
    ctl._on_result(ctl._generation + 1, result)
    assert not ctl.is_checking
    assert fetch.calls == 0


def test_error_delivered_as_user_message(qapp):
    import urllib.error
    fetch = _FakeFetch(
        b"",
        exc=urllib.error.URLError("connection refused"),
    )
    ready = [True]

    def is_ready():
        return ready[0]

    received = []
    ctl = CatalogController(is_ready=is_ready, fetch=fetch)
    ctl.check_failed.connect(lambda msg: received.append(msg))
    assert ctl.check() is True
    # Simulate async completion: deliver the error CatalogResult directly
    # to the coordinator.
    result = CatalogResult(error="No se puede conectar con CastleArq Core")
    ctl._on_result(ctl._generation, result)
    assert len(received) == 1
    assert "No se puede conectar" in received[0]


def test_result_preserved_on_error(qapp):
    entry = ModelEntry(
        model_id="model-a",
        name="Model A",
        provider="Prov",
        family="Fam",
        task="general-purpose",
        architecture="Transformer",
        parameter_count_b=7.0,
        context_length=32768,
        supported_runtimes=("llama.cpp / llama.app",),
        supported_backends=("CPU",),
        quantizations=("Q4_K_M",),
        downloadable=True,
        download_source="huggingface",
        download_repository="prov/model-a",
    )
    fetch = _FakeFetch(_make_result_json([{
        "model_id": "model-a",
        "name": "Model A",
        "provider": "Prov",
        "family": "Fam",
        "task": "general-purpose",
        "architecture": "Transformer",
        "parameter_count_b": 7.0,
        "context_length": 32768,
        "supported_runtimes": ["llama.cpp / llama.app"],
        "supported_backends": ["CPU"],
        "quantizations": ["Q4_K_M"],
        "downloadable": True,
        "download_source": "huggingface",
        "download_repository": "prov/model-a",
    }]))
    ready = [True]

    def is_ready():
        return ready[0]

    received = []
    ctl = CatalogController(is_ready=is_ready, fetch=fetch)
    ctl.check_finished.connect(lambda r: received.append(r))
    assert ctl.check() is True
    # Simulate async completion: deliver the result directly to the
    # coordinator.
    result = CatalogResult(models=(entry,))
    ctl._on_result(ctl._generation, result)
    assert len(received) == 1
    assert received[0].models is not None
    assert len(received[0].models) == 1


def test_real_qt_transport_delivers_result(qapp, qtbot):
    """Integración real: check() -> QThreadPool -> CatalogRunnable ->
    fetch_catalog() -> CatalogBridge.resolved -> _dispatch() ->
    CatalogController._on_result().

    Verifica que el trabajo se ejecuta en un hilo de trabajo y que el
    resultado se entrega al hilo de la interfaz mediante el transporte Qt,
    sin llamar directamente a _on_result().
    """
    received = []
    failed = []
    worker_threads = []
    gui_thread = QThread.currentThread()

    def fake_fetch(*, deadline=None):
        worker_threads.append(QThread.currentThread())
        return _make_result_json([])

    ctl = CatalogController(is_ready=lambda: True, fetch=fake_fetch)
    ctl.check_finished.connect(lambda r: received.append(r))
    ctl.check_failed.connect(lambda msg: failed.append(msg))

    assert ctl.check() is True
    assert ctl.is_checking

    qtbot.waitUntil(lambda: ctl._active_generation is None, timeout=5000)

    assert not ctl.is_checking
    assert len(received) == 1, f"expected exactly one result, got {received!r}"
    assert len(failed) == 0, f"unexpected failure signal: {failed!r}"
    assert received[0].models == ()
    assert worker_threads, "the worker never executed"
    assert worker_threads[0] is not gui_thread, (
        "the work did not run on a thread distinct from the GUI thread"
    )
    assert gui_thread is QThread.currentThread(), (
        "the callback was not processed on the GUI thread"
    )


__all__ = []