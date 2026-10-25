# Copyright 2026 Nicolas Bruna
#
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP client for CastleArq Core's persistent chat API.

Core owns the conversation memory. Desktop never simulates it: it opens a
**real** session on Core (``POST /v1/chat/sessions``), sends one turn at a
time (``POST /v1/chat/sessions/{id}/turns``) and closes the session
(``DELETE /v1/chat/sessions/{id}``). Because the session lives in Core, the
conversation really persists across turns: the second turn sees the first one.

The client is **synchronous** on purpose; the Qt worker that keeps it off
the GUI thread lives in :mod:`castlearq_desktop.core.chat_service`.

Transport is an injectable callable returning ``(status, body_bytes)``. The
default transport uses stdlib ``urllib``: ``httpx`` is NOT declared in
``pyproject.toml`` and new dependencies are not authorized.

Error translation is explicit and total: every HTTP status Core can answer
for these three routes is mapped to one actionable Spanish message. No new
``ErrorCode`` member is introduced: the approved stable code set (DEC-D) is
closed, so a chat failure is reported with its own typed error carrying the
observed status and a short reason, never with an invented code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional
from urllib import request as _urllib_request
from urllib.error import HTTPError as _UrllibHTTPError
from urllib.error import URLError as _UrllibURLError

from castlearq_desktop.core.contracts.port_conflict import CORE_HOST
from castlearq_desktop.core.contracts.port_conflict import CORE_PORT

__all__ = [
    "CHAT_SESSIONS_PATH",
    "ChatClientError",
    "ChatSessionInfo",
    "ChatTurnResult",
    "CoreChatClient",
    "default_chat_base_url",
    "translate_error",
    "urllib_chat_transport",
]

#: The ratified chat collection route of Core's HTTP API.
CHAT_SESSIONS_PATH = "/v1/chat/sessions"

#: Timeout for short control requests (session creation / deletion).
CONTROL_TIMEOUT_SECONDS = 30.0

#: Timeout for one turn: a generation may legitimately take minutes.
TURN_TIMEOUT_SECONDS = 600.0

#: Maximum body bytes read from a Core answer (a response may be long).
MAX_BODY_BYTES = 8 * 1024 * 1024


class ChatClientError(Exception):
    """A chat operation that Core refused, or that never reached Core.

    ``user_message`` is the only thing the UI shows. ``status`` is the
    observed HTTP status (``None`` for transport failures) and ``reason`` is a
    short, stable machine-readable token.
    """

    def __init__(
        self,
        user_message: str,
        *,
        status: Optional[int] = None,
        reason: str = "error",
    ) -> None:
        self.user_message = user_message
        self.status = status
        self.reason = reason
        super().__init__(user_message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.user_message


class CoreChatClient:
    """Synchronous client of Core's three chat routes.

    The transport is injectable so tests never open a socket. Every method
    raises :class:`ChatClientError` on any non-success status, and returns a
    typed DTO on success.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        transport: Optional[Callable[..., tuple[int, Optional[bytes]]]] = None,
    ) -> None:
        self._base_url = base_url or default_chat_base_url()
        self._transport = transport or urllib_chat_transport(self._base_url)

    @property
    def base_url(self) -> str:
        """The Core base URL this client talks to."""
        return self._base_url

    # -- routes ------------------------------------------------------------

    def create_session(
        self,
        model_id: str,
        *,
        filename: Optional[str] = None,
        quantization: Optional[str] = None,
        device: Optional[str] = None,

    ) -> ChatSessionInfo:
        """Open a chat session on Core for *model_id*.

        ``filename`` and ``quantization`` are the logical selectors Core
        itself uses to pick the artifact; Desktop passes exactly what Core
        reported for the selected artifact and never invents a path.
        """
        if not isinstance(model_id, str) or not model_id.strip():
            raise ChatClientError(
                "No hay un modelo seleccionado para iniciar el chat.",
                reason="invalid_request",
            )
        payload: dict[str, Any] = {"model_id": model_id}
        if filename:
            payload["filename"] = filename
        if quantization:
            payload["quantization"] = quantization
        if device:
            payload["device"] = device

        status, body = self._request(
            "POST", CHAT_SESSIONS_PATH, payload, CONTROL_TIMEOUT_SECONDS
        )
        if status != 201:
            raise translate_error(status, body)
        data = _decode_body(body)
        if not isinstance(data, Mapping):
            raise ChatClientError(
                "El Core respondió con una sesión de chat ilegible.",
                status=status,
                reason="malformed_response",
            )
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ChatClientError(
                "El Core respondió sin identificador de sesión de chat.",
                status=status,
                reason="malformed_response",
            )
        reported_model = data.get("model_id")
        turn_count = data.get("turn_count")
        return ChatSessionInfo(
            session_id=session_id,
            model_id=reported_model if isinstance(reported_model, str) else model_id,
            status=data.get("status") if isinstance(data.get("status"), str) else "",
            turn_count=turn_count if isinstance(turn_count, int) else 0,
        )

    def send_turn(self, session_id: str, prompt: str) -> ChatTurnResult:
        """Run one turn on an existing session and return Core's answer."""
        if not isinstance(session_id, str) or not session_id:
            raise ChatClientError("No hay una sesión de chat abierta.", reason="no_session")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ChatClientError(
                "Escribe un mensaje antes de enviarlo.", reason="empty_prompt"
            )
        path = f"{CHAT_SESSIONS_PATH}/{session_id}/turns"
        status, body = self._request(
            "POST", path, {"prompt": prompt}, TURN_TIMEOUT_SECONDS
        )
        if status != 200:
            raise translate_error(status, body)
        data = _decode_body(body)
        if not isinstance(data, Mapping):
            raise ChatClientError(
                "El Core respondió con un turno ilegible.",
                status=status,
                reason="malformed_response",
            )
        response = data.get("response")
        turn_count = data.get("turn_count")
        return ChatTurnResult(
            session_id=session_id,
            response=response if isinstance(response, str) else "",
            turn_count=turn_count if isinstance(turn_count, int) else 0,
        )

    def delete_session(self, session_id: str) -> None:
        """Close the session on Core so it releases its runtime."""
        if not isinstance(session_id, str) or not session_id:
            return
        path = f"{CHAT_SESSIONS_PATH}/{session_id}"
        status, body = self._request("DELETE", path, None, CONTROL_TIMEOUT_SECONDS)
        if status in (204, 404):
            # 404: the session is already gone; closing is idempotent.
            return
        raise translate_error(status, body)

    # -- internals ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]],
        timeout: float,
    ) -> tuple[int, Optional[bytes]]:
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        try:
            return self._transport(method, path, body, timeout)
        except ChatClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak internals
            raise ChatClientError(
                "No se pudo completar la operación de chat con CastleArq Core.",
                reason="transport_error",
            ) from exc


def default_chat_base_url() -> str:
    """Return the fixed loopback base URL of Core (DEC-G constants)."""
    return f"http://{CORE_HOST}:{CORE_PORT}"


def _decode_body(raw: Any) -> Any:
    """Decode a JSON body, returning ``None`` when it is not usable JSON."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - defensive only
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _error_text(payload: Any) -> Optional[str]:
    """Return Core's own ``error`` field when it sent a JSON body."""
    if isinstance(payload, Mapping):
        message = payload.get("error")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None


def translate_error(status: int, body: Any = None) -> ChatClientError:
    """Map one observed HTTP status to one actionable message.

    The mapping covers every status Core's chat routes can answer
    (400/403/404/409/422/500/503) plus an unknown-status fallback. Core's own
    ``error`` string is used only to enrich the message when it is safe to
    show; it never replaces the explanation of what the user should do.
    """
    payload = _decode_body(body)
    detail = _error_text(payload)
    if status == 400:
        return ChatClientError(
            "Core rechazó la petición del chat (datos no válidos).",
            status=status,
            reason="invalid_request",
        )
    if status == 403:
        return ChatClientError(
            "El Core no autorizó este artefacto para ejecución; "
            "revisa la compatibilidad del modelo.",
            status=status,
            reason="not_admitted",
        )
    if status == 404:
        return ChatClientError(
            "El Core no encontró el modelo o el artefacto indicado.",
            status=status,
            reason="not_found",
        )
    if status == 409:
        return ChatClientError(
            "El Core está ocupado con otra operación de chat; "
            "espera a que termine.",
            status=status,
            reason="busy",
        )
    if status == 422:
        return ChatClientError(
            "El Core no pudo preparar la ejecución del artefacto.",
            status=status,
            reason="preparation_failed",
        )
    if status == 500:
        return ChatClientError(
            "El Core falló al procesar la operación de chat.",
            status=status,
            reason="core_failure",
        )
    if status == 503:
        return ChatClientError(
            "El runtime de chat del Core no está disponible en este momento.",
            status=status,
            reason="runtime_unavailable",
        )
    if status == 204:
        return ChatClientError(
            "El Core respondió sin contenido a una operación de chat.",
            status=status,
            reason="empty_response",
        )
    message = "El Core rechazó la operación de chat."
    if detail:
        message = f"{message} ({detail})"
    return ChatClientError(message, status=status, reason="http_error")


def urllib_chat_transport(
    base_url: str,
) -> Callable[..., tuple[int, Optional[bytes]]]:
    """Build the default stdlib transport bound to *base_url*.

    The returned callable has the signature
    ``transport(method, path, body, timeout)`` and returns
    ``(status, body_bytes)``. HTTP error statuses are **not** raised: they
    are returned, so the caller can translate them through
    :func:`translate_error`.
    """

    def _transport(
        method: str,
        path: str,
        body: Optional[bytes] = None,
        timeout: float = CONTROL_TIMEOUT_SECONDS,
    ) -> tuple[int, Optional[bytes]]:
        url = base_url.rstrip("/") + path
        request = _urllib_request.Request(url, data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        try:
            with _urllib_request.urlopen(
                request, timeout=max(0.0, float(timeout))
            ) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
                return int(response.status), raw
        except _UrllibHTTPError as exc:
            try:
                raw = exc.read(MAX_BODY_BYTES + 1)
            except Exception:  # noqa: BLE001 - body is best effort
                raw = None
            return int(exc.code), raw
        except _UrllibURLError as exc:
            raise ChatClientError(
                "No se puede conectar con CastleArq Core.",
                status=None,
                reason="unreachable",
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise ChatClientError(
                "No se pudo completar la operación de chat con CastleArq Core.",
                status=None,
                reason="transport_error",
            ) from exc

    return _transport



@dataclass(frozen=True)
class ChatSessionInfo:
    """Core's own view of an opened session (never paths or processes)."""

    session_id: str
    model_id: str
    status: str
    turn_count: int = 0


@dataclass(frozen=True)
class ChatTurnResult:
    """The completed turn, exactly as Core reported it."""

    session_id: str
    response: str
    turn_count: int
