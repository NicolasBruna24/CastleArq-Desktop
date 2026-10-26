from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple
from urllib import request as _urllib_request
from urllib.error import HTTPError as _UrllibHTTPError
from urllib.error import URLError as _UrllibURLError

from castlearq_desktop.core.contracts.port_conflict import CORE_HOST
from castlearq_desktop.core.contracts.port_conflict import CORE_PORT

__all__ = [
    "OPENVINO_INFER_PATH", "OpenVINOClientError", "OpenVINOInferResult",
    "CoreOpenVINOClient", "default_openvino_base_url", "translate_error",
    "urllib_openvino_transport",
]

OPENVINO_INFER_PATH = "/v1/openvino/infer"
CONTROL_TIMEOUT_SECONDS = 30.0
INFERENCE_TIMEOUT_SECONDS = 600.0
MAX_BODY_BYTES = 8 * 1024 * 1024
VALID_DEVICES = ("CPU", "GPU")


class OpenVINOClientError(Exception):
    def __init__(self, user_message, *, status=None, reason="error"):
        self.user_message = user_message
        self.status = status
        self.reason = reason
        super().__init__(user_message)


@dataclass(frozen=True)
class OpenVINOInferResult:
    text: str
    model_id: str
    effective_device: str
    finish_reason: Optional[str] = None


def _extract_detail(body):
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, str):
            return err
    return None


def translate_error(status, body=None):
    detail = _extract_detail(body)
    if status is None:
        return OpenVINOClientError("No se puede conectar con CastleArq Core.", status=None, reason="unreachable")
    if status == 400:
        msg = "Solicitud OpenVINO inválida."
        if detail:
            msg = f"{msg} ({detail})"
        return OpenVINOClientError(msg, status=status, reason="bad_request")
    if status == 404:
        return OpenVINOClientError("Modelo OpenVINO no encontrado en Core.", status=status, reason="not_found")
    if status == 422:
        msg = "El artefacto no es compatible con OpenVINO."
        if detail:
            msg = f"{msg} ({detail})"
        return OpenVINOClientError(msg, status=status, reason="invalid_artifact")
    if status == 503:
        return OpenVINOClientError("OpenVINO no está disponible en Core.", status=status, reason="unavailable")
    if status == 500:
        return OpenVINOClientError("Core falló al procesar la inferencia OpenVINO.", status=status, reason="core_failure")
    msg = "El Core rechazó la operación OpenVINO."
    if detail:
        msg = f"{msg} ({detail})"
    return OpenVINOClientError(msg, status=status, reason="http_error")


def default_openvino_base_url():
    return f"http://{CORE_HOST}:{CORE_PORT}"


class CoreOpenVINOClient:
    def __init__(self, base_url, transport=None):
        self._base_url = base_url.rstrip("/")
        self._transport = transport or urllib_openvino_transport(self._base_url)

    def infer(self, model_id, prompt, device="CPU", max_tokens=512, timeout=INFERENCE_TIMEOUT_SECONDS):
        if not isinstance(model_id, str) or not model_id.strip():
            raise OpenVINOClientError("model_id es obligatorio.", reason="validation")
        if not isinstance(prompt, str) or not prompt.strip():
            raise OpenVINOClientError("prompt es obligatorio.", reason="validation")
        if device not in VALID_DEVICES:
            raise OpenVINOClientError("device debe ser CPU o GPU.", reason="validation")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0 or max_tokens > 2048:
            raise OpenVINOClientError("max_tokens debe ser un entero entre 1 y 2048.", reason="validation")
        body = {"model_id": model_id.strip(), "prompt": prompt, "device": device, "max_tokens": max_tokens}
        raw = json.dumps(body).encode("utf-8")
        try:
            status, resp = self._transport("POST", OPENVINO_INFER_PATH, raw, timeout)
        except OpenVINOClientError:
            raise
        except Exception as exc:
            raise OpenVINOClientError("No se pudo completar la operación OpenVINO.", status=None, reason="transport_error") from exc
        if status != 200:
            raise translate_error(status, resp)
        try:
            payload = json.loads((resp or b"").decode("utf-8"))
        except Exception:
            raise OpenVINOClientError("Respuesta inválida de Core.", status=status, reason="invalid_response")
        if not isinstance(payload, dict):
            raise OpenVINOClientError("Respuesta inválida de Core.", status=status, reason="invalid_response")
        text = payload.get("text")
        mid = payload.get("model_id")
        dev = payload.get("effective_device", "unknown")
        finish_reason = payload.get("finish_reason")
        if not isinstance(text, str) or not isinstance(mid, str):
            raise OpenVINOClientError("Respuesta inválida de Core.", status=status, reason="invalid_response")
        return OpenVINOInferResult(text=text, model_id=mid, effective_device=str(dev), finish_reason=finish_reason if isinstance(finish_reason, (str, type(None))) else str(finish_reason))


def urllib_openvino_transport(base_url):
    def _transport(method, path, body=None, timeout=CONTROL_TIMEOUT_SECONDS):
        url = base_url.rstrip("/") + path
        req = _urllib_request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with _urllib_request.urlopen(req, timeout=max(0.0, float(timeout))) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
                return int(response.status), raw
        except _UrllibHTTPError as exc:
            try:
                raw = exc.read(MAX_BODY_BYTES + 1)
            except Exception:
                raw = None
            return int(exc.code), raw
        except _UrllibURLError as exc:
            raise OpenVINOClientError("No se puede conectar con CastleArq Core.", status=None, reason="unreachable") from exc
        except (TimeoutError, OSError) as exc:
            raise OpenVINOClientError("No se pudo completar la operación OpenVINO con CastleArq Core.", status=None, reason="transport_error") from exc
    return _transport
