# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""HTTP client for GET /v1/models with injectable transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from urllib import request as _urllib_request
from urllib.error import HTTPError as _UrllibHTTPError
from urllib.error import URLError as _UrllibURLError

from castlearq_desktop.core.model_catalog import CatalogError, ModelEntry, parse_catalog_response

MODELS_URL = "http://127.0.0.1:8000/v1/models"
MAX_REQUEST_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class CatalogResult:
    models: Optional[tuple[ModelEntry, ...]] = None
    error: Optional[str] = None


def fetch_catalog(
    *,
    fetch: Optional[Callable[..., bytes]] = None,
    deadline: Optional[float] = None,
) -> CatalogResult:
    """Fetch and validate the model catalog from Core.

    *fetch* is called as ``fetch(deadline=...)`` and must return raw bytes
    or raise. Defaults to stdlib urllib against the loopback URL.
    """
    if fetch is None:
        fetch = _urllib_fetch

    try:
        body = fetch(deadline=deadline)
    except TimeoutError:
        return CatalogResult(error="El plazo de consulta ha expirado")
    except _UrllibHTTPError as exc:
        return CatalogResult(error=f"Error HTTP al consultar modelos ({exc.code})")
    except _UrllibURLError:
        return CatalogResult(error="No se puede conectar con CastleArq Core")
    except Exception:
        return CatalogResult(error="No se puede conectar con CastleArq Core")

    try:
        import json
        decoded = json.loads(body.decode("utf-8"))
    except Exception:
        return CatalogResult(error="La respuesta del catálogo no tiene un formato válido")

    try:
        models = parse_catalog_response(decoded)
    except CatalogError:
        return CatalogResult(error="La respuesta del catálogo no tiene un formato válido")

    return CatalogResult(models=models)


def _urllib_fetch(*, deadline: Optional[float] = None) -> bytes:
    import time
    timeout = MAX_REQUEST_TIMEOUT_SECONDS
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("deadline expired")
        timeout = min(timeout, remaining)
    req = _urllib_request.Request(MODELS_URL, method="GET")
    with _urllib_request.urlopen(req, timeout=timeout) as response:
        return response.read()


__all__ = ["CatalogResult", "CatalogError", "MODELS_URL", "fetch_catalog"]