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

"""Tests for Core's chat HTTP client.

The transport is always faked: no socket is opened and no real inference is
attempted. The fixtures replay the response shapes Core's own chat routes
answer with, so the contract is checked against the real one.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from castlearq_desktop.core.chat_client import (
    CHAT_SESSIONS_PATH,
    ChatClientError,
    CoreChatClient,
    default_chat_base_url,
    translate_error,
)

SESSION_ID = "0f0d1b2c-3d4e-4f50-8a1b-2c3d4e5f6071"


class RecordingTransport:
    """Fake transport recording every call and replaying fixed answers."""

    def __init__(self, answers: list[tuple[int, Optional[bytes]]]) -> None:
        self._answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        timeout: float = 30.0,
    ) -> tuple[int, Optional[bytes]]:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": json.loads(body.decode("utf-8")) if body else None,
                "timeout": timeout,
            }
        )
        if not self._answers:
            raise AssertionError("unexpected extra HTTP call")
        return self._answers.pop(0)


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


# -- session creation -------------------------------------------------------


def test_create_session_posts_the_logical_selectors() -> None:
    transport = RecordingTransport(
        [
            (
                201,
                _json(
                    {
                        "session_id": SESSION_ID,
                        "model_id": "demo-model",
                        "status": "ready",
                    }
                ),
            )
        ]
    )
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)

    info = client.create_session(
        "demo-model", filename="demo.gguf", quantization="Q4_K_M"
    )

    assert info.session_id == SESSION_ID
    assert info.model_id == "demo-model"
    assert info.status == "ready"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == CHAT_SESSIONS_PATH
    assert call["body"] == {
        "model_id": "demo-model",
        "filename": "demo.gguf",
        "quantization": "Q4_K_M",
    }


def test_create_session_omits_absent_selectors() -> None:
    transport = RecordingTransport(
        [(201, _json({"session_id": SESSION_ID, "model_id": "m", "status": "ready"}))]
    )
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)

    client.create_session("m")

    assert transport.calls[0]["body"] == {"model_id": "m"}


def test_create_session_requires_a_model() -> None:
    client = CoreChatClient(
        "http://127.0.0.1:8000", transport=RecordingTransport([])
    )
    with pytest.raises(ChatClientError) as excinfo:
        client.create_session("   ")
    assert excinfo.value.reason == "invalid_request"


def test_create_session_rejects_a_missing_session_id() -> None:
    transport = RecordingTransport([(201, _json({"model_id": "m"}))])
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)
    with pytest.raises(ChatClientError) as excinfo:
        client.create_session("m")
    assert excinfo.value.reason == "malformed_response"


# -- turns ------------------------------------------------------------------


def test_send_turn_posts_the_prompt_and_reads_the_answer() -> None:
    transport = RecordingTransport(
        [
            (
                200,
                _json(
                    {
                        "session_id": SESSION_ID,
                        "response": "hola",
                        "turn_count": 1,
                    }
                ),
            )
        ]
    )
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)

    result = client.send_turn(SESSION_ID, "¿qué tal?")

    assert result.response == "hola"
    assert result.turn_count == 1
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"{CHAT_SESSIONS_PATH}/{SESSION_ID}/turns"
    assert call["body"] == {"prompt": "¿qué tal?"}


def test_send_turn_rejects_an_empty_prompt() -> None:
    client = CoreChatClient(
        "http://127.0.0.1:8000", transport=RecordingTransport([])
    )
    with pytest.raises(ChatClientError) as excinfo:
        client.send_turn(SESSION_ID, "   ")
    assert excinfo.value.reason == "empty_prompt"


def test_send_turn_requires_a_session() -> None:
    client = CoreChatClient(
        "http://127.0.0.1:8000", transport=RecordingTransport([])
    )
    with pytest.raises(ChatClientError) as excinfo:
        client.send_turn("", "hola")
    assert excinfo.value.reason == "no_session"


def test_an_empty_response_is_not_an_error() -> None:
    transport = RecordingTransport(
        [(200, _json({"session_id": SESSION_ID, "response": "", "turn_count": 2}))]
    )
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)
    result = client.send_turn(SESSION_ID, "hola")
    assert result.response == ""
    assert result.turn_count == 2


def test_a_malformed_turn_body_is_refused() -> None:
    transport = RecordingTransport([(200, b"not json")])
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)
    with pytest.raises(ChatClientError) as excinfo:
        client.send_turn(SESSION_ID, "hola")
    assert excinfo.value.reason == "malformed_response"


# -- session deletion -------------------------------------------------------


def test_delete_session_uses_the_delete_verb() -> None:
    transport = RecordingTransport([(204, None)])
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)

    client.delete_session(SESSION_ID)

    call = transport.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == f"{CHAT_SESSIONS_PATH}/{SESSION_ID}"
    assert call["body"] is None


def test_delete_session_is_idempotent_for_an_unknown_session() -> None:
    transport = RecordingTransport([(404, None)])
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)
    client.delete_session(SESSION_ID)  # must not raise


def test_delete_session_without_an_id_does_nothing() -> None:
    transport = RecordingTransport([])
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)
    client.delete_session("")
    assert transport.calls == []


# -- error translation ------------------------------------------------------


@pytest.mark.parametrize(
    "status,reason",
    [
        (400, "invalid_request"),
        (403, "not_admitted"),
        (404, "not_found"),
        (409, "busy"),
        (422, "preparation_failed"),
        (500, "core_failure"),
        (503, "runtime_unavailable"),
    ],
)
def test_every_core_status_maps_to_an_actionable_message(
    status: int, reason: str
) -> None:
    error = translate_error(status, b"")
    assert error.status == status
    assert error.reason == reason
    assert error.user_message.strip()


def test_an_unknown_status_still_produces_a_message() -> None:
    error = translate_error(418, b"")
    assert error.reason == "http_error"
    assert error.user_message.strip()


def test_a_failed_turn_raises_the_translated_error() -> None:
    transport = RecordingTransport([(409, b"")])
    client = CoreChatClient("http://127.0.0.1:8000", transport=transport)
    with pytest.raises(ChatClientError) as excinfo:
        client.send_turn(SESSION_ID, "hola")
    assert excinfo.value.status == 409
    assert excinfo.value.reason == "busy"


def test_a_transport_failure_is_reported_without_a_status() -> None:
    def boom(method, path, body=None, timeout=30.0):
        raise OSError("connection reset")

    client = CoreChatClient("http://127.0.0.1:8000", transport=boom)
    with pytest.raises(ChatClientError) as excinfo:
        client.create_session("m")
    assert excinfo.value.status is None
    assert excinfo.value.reason == "transport_error"


def test_the_default_base_url_is_the_loopback_core() -> None:
    assert default_chat_base_url() == "http://127.0.0.1:8000"
