# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Model catalog contract for GET /v1/models (read-only).

Defines ModelEntry and parse_catalog_response. No network, no Qt, no GUI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

MODELS_KEY = "models"
_REQUIRED_STRING_FIELDS = ("model_id", "name", "provider", "family", "task", "architecture")
_REQUIRED_ARRAY_FIELDS = ("supported_runtimes", "supported_backends", "quantizations")
_OPTIONAL_NULLABLE_FIELDS = ("parameter_count_b", "context_length", "download_source", "download_repository")
_REQUIRED_BOOL_FIELDS = ("downloadable",)


class CatalogError(Exception):
    def __init__(self, user_message: str, *, technical_context: Optional[Mapping[str, Any]] = None) -> None:
        self.user_message = user_message
        self.technical_context: Mapping[str, Any] = dict(technical_context or {})
        super().__init__(self.user_message)


@dataclass(frozen=True)
class ModelEntry:
    model_id: str
    name: str
    provider: str
    family: str
    task: str
    architecture: str
    parameter_count_b: Optional[float] = None
    context_length: Optional[int] = None
    supported_runtimes: tuple[str, ...] = ()
    supported_backends: tuple[str, ...] = ()
    quantizations: tuple[str, ...] = ()
    downloadable: bool = False
    download_source: Optional[str] = None
    download_repository: Optional[str] = None


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _require_string(value: Any, field: str, source: Any) -> str:
    if not isinstance(value, str):
        raise CatalogError(
            f"campo '{field}' debe ser texto",
            technical_context={"field": field, "expected": "str", "received_type": type(value).__name__, "entry": source},
        )
    return value


def _require_string_array(value: Any, field: str, source: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        raise CatalogError(
            f"campo '{field}' debe ser una lista de textos",
            technical_context={"field": field, "expected": "list[str]", "received_type": type(value).__name__, "entry": source},
        )
    return tuple(value)


def _require_number(value: Any, field: str, source: Any) -> float:
    if _is_bool(value) or not isinstance(value, (int, float)):
        raise CatalogError(
            f"campo '{field}' debe ser un número",
            technical_context={"field": field, "expected": "int|float (no bool)", "received_type": type(value).__name__, "entry": source},
        )
    result = float(value)
    if not math.isfinite(result):
        raise CatalogError(
            f"campo '{field}' debe ser un número finito",
            technical_context={"field": field, "expected": "finite number", "received": str(value), "entry": source},
        )
    return result


def _require_int(value: Any, field: str, source: Any) -> int:
    if _is_bool(value) or not isinstance(value, int):
        raise CatalogError(
            f"campo '{field}' debe ser un entero",
            technical_context={"field": field, "expected": "int (no bool)", "received_type": type(value).__name__, "entry": source},
        )
    if not math.isfinite(value):
        raise CatalogError(
            f"campo '{field}' debe ser un entero finito",
            technical_context={"field": field, "expected": "finite int", "received": str(value), "entry": source},
        )
    return value


def _require_bool(value: Any, field: str, source: Any) -> bool:
    if not isinstance(value, bool):
        raise CatalogError(
            f"campo '{field}' debe ser un booleano",
            technical_context={"field": field, "expected": "bool", "received_type": type(value).__name__, "entry": source},
        )
    return value


def parse_catalog_response(body: Any) -> tuple[ModelEntry, ...]:
    if not isinstance(body, dict):
        raise CatalogError(
            "la respuesta del catálogo no tiene un formato válido",
            technical_context={"expected": "object", "received_type": type(body).__name__},
        )
    if MODELS_KEY not in body:
        raise CatalogError(
            "la respuesta del catálogo no tiene un formato válido",
            technical_context={"missing_key": MODELS_KEY},
        )
    models_raw = body[MODELS_KEY]
    if not isinstance(models_raw, list):
        raise CatalogError(
            "la respuesta del catálogo no tiene un formato válido",
            technical_context={"field": MODELS_KEY, "expected": "list", "received_type": type(models_raw).__name__},
        )
    entries: list[ModelEntry] = []
    for index, item in enumerate(models_raw):
        if not isinstance(item, dict):
            raise CatalogError(
                "la respuesta del catálogo no tiene un formato válido",
                technical_context={"field": f"{MODELS_KEY}[{index}]", "expected": "object", "received_type": type(item).__name__},
            )
        values: dict[str, Any] = {}
        for field in _REQUIRED_STRING_FIELDS:
            if field not in item:
                raise CatalogError(
                    "la respuesta del catálogo no tiene un formato válido",
                    technical_context={"field": f"{MODELS_KEY}[{index}].{field}", "missing": True},
                )
            values[field] = _require_string(item[field], field, item)
        for field in _REQUIRED_ARRAY_FIELDS:
            if field not in item:
                raise CatalogError(
                    "la respuesta del catálogo no tiene un formato válido",
                    technical_context={"field": f"{MODELS_KEY}[{index}].{field}", "missing": True},
                )
            values[field] = _require_string_array(item[field], field, item)
        for field in _REQUIRED_BOOL_FIELDS:
            if field not in item:
                raise CatalogError(
                    "la respuesta del catálogo no tiene un formato válido",
                    technical_context={"field": f"{MODELS_KEY}[{index}].{field}", "missing": True},
                )
            values[field] = _require_bool(item[field], field, item)
        for field in _OPTIONAL_NULLABLE_FIELDS:
            raw_value = item.get(field)
            if raw_value is None:
                values[field] = None
                continue
            if field == "parameter_count_b":
                values[field] = _require_number(raw_value, field, item)
            elif field == "context_length":
                values[field] = _require_int(raw_value, field, item)
            else:
                values[field] = _require_string(raw_value, field, item)
        entries.append(ModelEntry(**values))  # type: ignore[arg-type]
    return tuple(entries)


__all__ = ["CatalogError", "ModelEntry", "MODELS_KEY", "parse_catalog_response"]