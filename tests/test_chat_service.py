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

"""Tests for the asynchronous chat service.

The client is always a double and the thread pool is always controlled by the
test, so the suite never opens a socket and never depends on real timing.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from castlearq_desktop.core.chat_client import (
    ChatClientError,
    ChatSessionInfo,
    ChatTurnResult,
)
from castlearq_desktop.core.chat_service import ChatService

SESSION_ID = "session-1"


class FakeClient:
    """Client double recording the calls and replaying scripted answers."""

    def __init__(
        self,
        *,
        info: Optional[ChatSessionInfo] = None,
        turn: Optional[ChatTurnResult] = None,
        error: Optional[ChatClientError] = None,
    ) -> None:
        self.info = info or ChatSessionInfo(SESSION_ID, "demo-model", "ready")
        self.turn = turn or ChatTurnResult(SESSION_ID, "respuesta", 1)
        self.error = error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def create_session(self, model_id: str, **kwargs: Any) -> ChatSessionInfo:
        self.calls.append(("create_session", (model_id, tuple(sorted(kwargs.items())))))
        if self.error is not None:
            raise self.error
        return self.info

    def send_turn(self, session_id: str, prompt: str) -> ChatTurnResult:
        self.calls.append(("send_turn", (session_id, prompt)))
        if self.error is not None:
            raise self.error
        return self.turn

    def delete_session(self, session_id: str) -> None:
        self.calls.append(("delete_session", (session_id,)))
        if self.error is not None:
            raise self.error


class ImmediatePool:
    """Thread-pool double running the runnable synchronously."""

    def __init__(self) -> None:
        self.runnables: list[Any] = []

    def start(self, runnable: Any) -> None:
        self.runnables.append(runnable)
        runnable.run()


class DeferredPool:
    """Thread-pool double holding the runnable until the test releases it."""

    def __init__(self) -> None:
        self.runnables: list[Any] = []

    def start(self, runnable: Any) -> None:
        self.runnables.append(runnable)

    def release(self, index: int = 0) -> None:
        self.runnables[index].run()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _service(client: FakeClient, pool: Any) -> ChatService:
    return ChatService(client=client, thread_pool_factory=lambda: pool)  # type: ignore[arg-type]


# -- opening a session ------------------------------------------------------


def test_opening_a_session_reports_the_session(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    opened: list[tuple[str, str]] = []
    service.session_opened.connect(lambda sid, mid: opened.append((sid, mid)))

    assert service.open_session("demo-model") is True

    assert opened == [(SESSION_ID, "demo-model")]
    assert service.has_session is True
    assert service.session_id == SESSION_ID
    assert service.is_busy is False


def test_opening_a_session_without_a_model_is_refused(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    failures: list[str] = []
    service.failed.connect(failures.append)

    assert service.open_session("  ") is False

    assert failures
    assert client.calls == []


def test_opening_while_a_session_is_active_is_refused(qapp) -> None:
    """A duplicate open is rejected without creating a second session."""
    client = FakeClient()
    service = _service(client, ImmediatePool())
    failures: list[str] = []
    service.failed.connect(failures.append)
    busy: list[bool] = []
    service.busy_changed.connect(busy.append)

    assert service.open_session("demo-model") is True
    creations = [call for call in client.calls if call[0] == "create_session"]
    busy_after_first = list(busy)
    generation = service._generation
    active_generation = service._active_generation

    # 1. The duplicate is refused with the regular rejection contract.
    assert service.open_session("demo-model") is False

    # 2. No second creation reached Core and the session is the same one.
    assert [call for call in client.calls if call[0] == "create_session"] == creations
    assert service.session_id == SESSION_ID
    assert service.has_session is True
    # The refusal issues no DELETE, publishes no state change, leaves the
    # generations untouched and reports no fake failure.
    assert all(call[0] != "delete_session" for call in client.calls)
    assert busy == busy_after_first
    assert service._generation == generation
    assert service._active_generation == active_generation
    assert service.is_busy is False
    assert failures == []


def test_a_session_refused_as_duplicate_is_still_closable(qapp) -> None:
    """After a refusal the original session closes through the normal flow."""
    client = FakeClient()
    service = _service(client, ImmediatePool())
    assert service.open_session("demo-model") is True
    assert service.open_session("demo-model") is False

    # 3. The normal close still deletes the open session on Core.
    assert service.close_session() is True

    assert service.has_session is False
    assert ("delete_session", (SESSION_ID,)) in client.calls
    # And a fresh session can be opened afterwards.
    assert service.open_session("demo-model") is True
    assert service.has_session is True


# -- turns ------------------------------------------------------------------


def test_a_turn_reports_the_answer_of_core(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    service.open_session("demo-model")
    answers: list[tuple[str, int]] = []
    service.turn_completed.connect(lambda text, count: answers.append((text, count)))

    assert service.send_turn("hola") is True

    assert answers == [("respuesta", 1)]
    assert ("send_turn", (SESSION_ID, "hola")) in client.calls


def test_a_turn_without_a_session_is_refused(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    failures: list[str] = []
    service.failed.connect(failures.append)

    assert service.send_turn("hola") is False

    assert failures
    assert client.calls == []


def test_an_empty_prompt_is_refused(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    service.open_session("demo-model")
    failures: list[str] = []
    service.failed.connect(failures.append)

    assert service.send_turn("   ") is False

    assert failures


# -- single flight ----------------------------------------------------------


def test_only_one_operation_runs_at_a_time(qapp) -> None:
    client = FakeClient()
    pool = DeferredPool()
    service = _service(client, pool)

    assert service.open_session("demo-model") is True
    assert service.is_busy is True
    # A second operation while one is in flight is refused, not queued.
    assert service.open_session("otro-model") is False
    assert service.send_turn("hola") is False

    assert len(pool.runnables) == 1


def test_the_busy_state_is_published(qapp) -> None:
    client = FakeClient()
    pool = DeferredPool()
    service = _service(client, pool)
    busy: list[bool] = []
    service.busy_changed.connect(busy.append)

    service.open_session("demo-model")
    pool.release()

    assert busy == [True, False]


# -- stale results ----------------------------------------------------------


def test_a_result_of_an_invalidated_operation_is_dropped(qapp) -> None:
    client = FakeClient()
    pool = DeferredPool()
    service = _service(client, pool)
    opened: list[tuple[str, str]] = []
    service.session_opened.connect(lambda sid, mid: opened.append((sid, mid)))

    service.open_session("demo-model")
    service.invalidate()
    pool.release()

    assert opened == []
    assert service.has_session is False


def test_a_late_failure_of_a_superseded_operation_is_dropped(qapp) -> None:
    client = FakeClient(error=ChatClientError("fallo viejo", reason="busy"))
    pool = DeferredPool()
    service = _service(client, pool)
    failures: list[str] = []
    service.failed.connect(failures.append)

    service.open_session("demo-model")
    service.invalidate()
    pool.release()

    assert failures == []


def test_a_late_failure_does_not_affect_a_later_operation(qapp) -> None:
    client = FakeClient()
    pool = DeferredPool()
    service = _service(client, pool)
    failures: list[str] = []
    service.failed.connect(failures.append)

    # First operation: it will fail late, after a second one took over.
    client.error = ChatClientError("fallo viejo", reason="busy")
    service.open_session("demo-model")
    first = pool.runnables[0]

    # Second operation supersedes the first.
    client.error = None
    service.invalidate()
    service.open_session("demo-model")
    second = pool.runnables[1]

    first.run()  # the stale failure arrives now
    assert failures == []

    second.run()
    assert failures == []


# -- closing ----------------------------------------------------------------


def test_closing_a_session_deletes_it_on_core(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    service.open_session("demo-model")

    assert service.close_session() is True

    assert ("delete_session", (SESSION_ID,)) in client.calls
    assert service.has_session is False


def test_closing_without_a_session_does_nothing(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())

    assert service.close_session() is False

    assert client.calls == []


def test_closing_without_a_session_leaves_the_service_usable(qapp) -> None:
    """Regression: a legitimate no-op close must never leave the service busy.

    Closing with no session starts no operation, so the active generation
    must stay untouched: otherwise the service would report itself busy
    forever and silently refuse every later open/turn.
    """
    client = FakeClient()
    service = _service(client, ImmediatePool())
    failures: list[str] = []
    service.failed.connect(failures.append)
    busy: list[bool] = []
    service.busy_changed.connect(busy.append)

    # 1. Closing without a session is refused...
    assert service.close_session() is False

    # 2. ...and leaves no phantom operation behind.
    assert service.is_busy is False
    # 3. No DELETE is requested, no state change is published and no
    #    artificial failure is reported for a legitimate no-op close.
    assert client.calls == []
    assert busy == []
    assert failures == []

    # 4. A later open is accepted and really creates the session.
    opened: list[tuple[str, str]] = []
    service.session_opened.connect(lambda sid, mid: opened.append((sid, mid)))
    assert service.open_session("demo-model") is True

    assert opened == [(SESSION_ID, "demo-model")]
    assert service.has_session is True
    assert service.session_id == SESSION_ID
    assert service.is_busy is False
    # Both states of the open were published. With the synchronous pool
    # double, the operation completes inside start() before _start()
    # publishes its busy pulse, so the pair is observed in reverse order;
    # is_busy above is the authoritative idle check.
    assert set(busy) == {True, False}


def test_closing_an_existing_session_deletes_it_and_publishes_state(qapp) -> None:
    """Closing a real session still issues the DELETE and publishes its state."""
    client = FakeClient()
    service = _service(client, ImmediatePool())
    service.open_session("demo-model")
    busy: list[bool] = []
    service.busy_changed.connect(busy.append)
    closed: list[bool] = []
    service.closed.connect(lambda: closed.append(True))
    failures: list[str] = []
    service.failed.connect(failures.append)

    assert service.close_session() is True

    assert ("delete_session", (SESSION_ID,)) in client.calls
    assert service.has_session is False
    assert service.is_busy is False
    # The close operation completes without a busy pulse (keep_busy=False)
    # and publishes the closed state; no failure is reported.
    assert busy == [False]
    assert closed == [True]
    assert failures == []
    # The service remains usable for a new session afterwards.
    assert service.open_session("demo-model") is True


def test_closing_discards_the_in_flight_turn(qapp) -> None:
    client = FakeClient()
    pool = DeferredPool()
    service = _service(client, pool)
    service.open_session("demo-model")
    pool.release()
    service.send_turn("hola")

    service.close_session()
    pool.release()

    # The answer of the discarded turn never reaches the service.
    assert service.session_id is None


def test_close_deletes_the_session_and_stops_accepting_work(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    service.open_session("demo-model")

    service.close()

    assert ("delete_session", (SESSION_ID,)) in client.calls
    assert service.open_session("demo-model") is False
    assert service.send_turn("hola") is False


def test_close_emits_the_closed_signal(qapp) -> None:
    client = FakeClient()
    service = _service(client, ImmediatePool())
    closed: list[bool] = []
    service.closed.connect(lambda: closed.append(True))

    service.close()

    assert closed == [True]


# -- failures ---------------------------------------------------------------


def test_a_core_failure_is_reported_to_the_view(qapp) -> None:
    client = FakeClient(error=ChatClientError("el Core está ocupado", reason="busy"))
    service = _service(client, ImmediatePool())
    failures: list[str] = []
    service.failed.connect(failures.append)

    service.open_session("demo-model")

    assert failures == ["el Core está ocupado"]
    assert service.has_session is False
    assert service.is_busy is False
