# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Tests for the catalog client and contract validation."""

from __future__ import annotations

import json
import time
from typing import Optional
from unittest import mock

import pytest

from castlearq_desktop.core.catalog_client import (
    CatalogResult,
    MODELS_URL,
    fetch_catalog,
)
from castlearq_desktop.core.model_catalog import CatalogError, parse_catalog_response


def _entry(
    model_id: str = "qwen2.5-coder-7b-instruct",
    name: str = "Qwen2.5-Coder 7B Instruct",
    task: str = "coding",
    parameter_count_b: Optional[float] = 7.0,
    context_length: Optional[int] = 32768,
    supported_runtimes: Optional[list[str]] = None,
    supported_backends: Optional[list[str]] = None,
    quantizations: Optional[list[str]] = None,
    downloadable: bool = True,
    download_source: Optional[str] = "huggingface",
    download_repository: Optional[str] = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
) -> dict:
    return {
        "model_id": model_id,
        "name": name,
        "provider": "Qwen",
        "family": "Qwen2.5-Coder",
        "task": task,
        "architecture": "Transformer",
        "parameter_count_b": parameter_count_b,
        "context_length": context_length,
        "supported_runtimes": supported_runtimes if supported_runtimes is not None else ["llama.cpp / llama.app", "Ollama"],
        "supported_backends": supported_backends if supported_backends is not None else ["Vulkan", "CUDA", "ROCm", "CPU"],
        "quantizations": quantizations if quantizations is not None else ["Q2_K", "Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"],
        "downloadable": downloadable,
        "download_source": download_source,
        "download_repository": download_repository,
    }


# --- parse_catalog_response ---


def test_parse_valid_catalog():
    body = {"models": [_entry(), _entry(model_id="llama-3.1-8b-instruct", name="Llama 3.1 8B Instruct", task="general-purpose")]}
    entries = parse_catalog_response(body)
    assert len(entries) == 2
    assert entries[0].model_id == "qwen2.5-coder-7b-instruct"
    assert entries[0].name == "Qwen2.5-Coder 7B Instruct"
    assert entries[0].provider == "Qwen"
    assert entries[0].family == "Qwen2.5-Coder"
    assert entries[0].task == "coding"
    assert entries[0].architecture == "Transformer"
    assert entries[0].parameter_count_b == 7.0
    assert entries[0].context_length == 32768
    assert entries[0].supported_runtimes == ("llama.cpp / llama.app", "Ollama")
    assert entries[0].supported_backends == ("Vulkan", "CUDA", "ROCm", "CPU")
    assert entries[0].quantizations == ("Q2_K", "Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0")
    assert entries[0].downloadable is True
    assert entries[0].download_source == "huggingface"
    assert entries[0].download_repository == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert entries[1].model_id == "llama-3.1-8b-instruct"
    assert entries[1].downloadable is True


def test_parse_empty_catalog():
    entries = parse_catalog_response({"models": []})
    assert entries == ()


def test_parse_root_not_object():
    with pytest.raises(CatalogError):
        parse_catalog_response(["not", "an", "object"])


def test_parse_models_missing():
    with pytest.raises(CatalogError):
        parse_catalog_response({})


def test_parse_models_not_list():
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": "not-a-list"})


def test_parse_entry_not_object():
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": ["not-an-object"]})


def test_parse_missing_required_string():
    entry = _entry()
    del entry["name"]
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_missing_required_array():
    entry = _entry()
    del entry["supported_runtimes"]
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_missing_required_bool():
    entry = _entry()
    del entry["downloadable"]
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_nullable_null():
    entry = _entry(parameter_count_b=None, context_length=None, download_source=None, download_repository=None)
    entries = parse_catalog_response({"models": [entry]})
    assert entries[0].parameter_count_b is None
    assert entries[0].context_length is None
    assert entries[0].download_source is None
    assert entries[0].download_repository is None


# --- fetch_catalog ---


def _make_fetch(body: bytes, exc: Optional[Exception] = None):
    def _fetch(*, deadline: Optional[float] = None) -> bytes:
        if exc is not None:
            raise exc
        return body
    return _fetch


def test_fetch_catalog_success():
    body = json.dumps({"models": [_entry()]}).encode("utf-8")
    result = fetch_catalog(fetch=_make_fetch(body))
    assert result.error is None
    assert result.models is not None
    assert len(result.models) == 1
    assert result.models[0].model_id == "qwen2.5-coder-7b-instruct"


def test_fetch_catalog_empty():
    body = json.dumps({"models": []}).encode("utf-8")
    result = fetch_catalog(fetch=_make_fetch(body))
    assert result.error is None
    assert result.models == ()


def test_fetch_catalog_http_error():
    import urllib.error
    result = fetch_catalog(
        fetch=_make_fetch(b"", exc=urllib.error.HTTPError(MODELS_URL, 500, "Server Error", {}, None))
    )
    assert result.error is not None
    assert "500" in result.error
    assert result.models is None


def test_fetch_catalog_connection_error():
    import urllib.error
    result = fetch_catalog(
        fetch=_make_fetch(b"", exc=urllib.error.URLError("connection refused"))
    )
    assert result.error == "No se puede conectar con CastleArq Core"
    assert result.models is None


def test_fetch_catalog_invalid_json():
    result = fetch_catalog(fetch=_make_fetch(b"not json"))
    assert result.error == "La respuesta del catálogo no tiene un formato válido"
    assert result.models is None


def test_fetch_catalog_invalid_contract():
    body = json.dumps({"not_models": []}).encode("utf-8")
    result = fetch_catalog(fetch=_make_fetch(body))
    assert result.error == "La respuesta del catálogo no tiene un formato válido"
    assert result.models is None


def test_fetch_catalog_deadline_expired():
    def _fetch(*, deadline: Optional[float] = None) -> bytes:
        raise TimeoutError("deadline expired")
    result = fetch_catalog(fetch=_fetch, deadline=time.monotonic() - 1)
    assert result.error == "El plazo de consulta ha expirado"


def test_fetch_default_url():
    assert MODELS_URL == "http://127.0.0.1:8000/v1/models"


def test_parse_bool_where_number_expected():
    entry = _entry(parameter_count_b=True)
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_bool_where_int_expected():
    entry = _entry(context_length=True)
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_wrong_type_string_field():
    entry = _entry(model_id=123)
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_wrong_type_array_field():
    entry = _entry(supported_runtimes="not-a-list")
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_array_with_non_string():
    entry = _entry(supported_runtimes=["llama.cpp", 123])
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_nan_where_number_expected():
    entry = _entry(parameter_count_b=float("nan"))
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_inf_where_number_expected():
    entry = _entry(parameter_count_b=float("inf"))
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_neg_inf_where_number_expected():
    entry = _entry(parameter_count_b=float("-inf"))
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_nan_where_int_expected():
    entry = _entry(context_length=float("nan"))
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_inf_where_int_expected():
    entry = _entry(context_length=float("inf"))
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})


def test_parse_neg_inf_where_int_expected():
    entry = _entry(context_length=float("-inf"))
    with pytest.raises(CatalogError):
        parse_catalog_response({"models": [entry]})