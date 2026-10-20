# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""DEC-A tests: conservative version admission with test doubles only."""

from __future__ import annotations

import json

import pytest

from castlearq_desktop.core.contracts import ErrorCode
from castlearq_desktop.core.contracts import VersionAdmission
from castlearq_desktop.core.readiness_client import HttpProbeResponse
from castlearq_desktop.core.readiness_client import ReadinessHttpClient
from castlearq_desktop.core.version_admission import CANDIDATE_VERSION
from castlearq_desktop.core.version_admission import SUPPORTED_CORE_VERSIONS
from castlearq_desktop.core.version_admission import classify_version_admission
from castlearq_desktop.core.version_admission import evaluate_version_admission
from castlearq_desktop.core.version_admission import extract_health_version
from castlearq_desktop.core.version_admission import is_valid_version
from castlearq_desktop.core.version_admission import parse_installed_version_output
from castlearq_desktop.core.version_admission import query_installed_core_version


ADMITTED = "9.9.9-test"
ALLOW = frozenset({ADMITTED})


def health_body(version):
    return json.dumps({"version": version}).encode()


def sync_client(transport, *, installed, allow=ALLOW, **kwargs):
    kwargs.setdefault("child_observer", lambda: True)
    return ReadinessHttpClient(
        transport=transport,
        installed_version_provider=lambda: installed,
        supported_versions=allow,
        **kwargs)


def script_transport(health, *, models=200, artifacts=200):
    def _transport(path, *, timeout):
        if path == "/health":
            if isinstance(health, Exception):
                raise health
            if isinstance(health, tuple):
                status, body = health
                return HttpProbeResponse(status=status, body=body)
            return HttpProbeResponse(status=200, body=health)
        if path == "/v1/models":
            return HttpProbeResponse(status=models, body=b"{}")
        return HttpProbeResponse(status=artifacts, body=b"{}")
    return _transport


def test_supported_list_admits_candidate():
    assert SUPPORTED_CORE_VERSIONS == frozenset({"0.5.1"})
    assert CANDIDATE_VERSION == "0.5.1"
    assert CANDIDATE_VERSION in SUPPORTED_CORE_VERSIONS

def test_strict_version_syntax():
    assert is_valid_version("0.5.1")
    assert is_valid_version(ADMITTED)
    for bad in [None, "", "  ", " 0.5.1", "0.5.1 ",
                "a b", "v/1", 123, "x" * 65]:
        assert not is_valid_version(bad)


def test_installed_query_uses_selected_interpreter_only():
    calls = []

    class Completed:
        returncode = 0
        stdout = json.dumps({"distribution_version": "0.5.1"})

    def _execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return Completed()

    out = query_installed_core_version(
        "/abs/env/bin/python", execute=_execute)
    assert out == "0.5.1"
    argv, kwargs = calls[0]
    assert argv[0] == "/abs/env/bin/python"
    assert argv[1] == "-I" and argv[2] == "-c"
    assert kwargs["shell"] is False
    assert query_installed_core_version("python") is None
    assert parse_installed_version_output("not-json") is None
    assert parse_installed_version_output(
        json.dumps({"distribution_version": " 0.5.1"})) is None


def test_match_admitted_allows_ready():
    body = health_body(ADMITTED)
    c = sync_client(script_transport(body), installed=ADMITTED)
    out = c.probe_once(now=0.0)
    assert out.ready is True
    assert out.error_code is None


def test_mismatch_rejected_with_incompatible():
    c = sync_client(script_transport(health_body(ADMITTED)),
                    installed="0.5.0")
    out = c.probe_once(now=0.0)
    assert out.ready is False
    assert out.error_code is ErrorCode.API_CONTRACT_INCOMPATIBLE
    conds = [f.condition.value for f in out.outcome.failures]
    assert "api_contract" in conds


def test_matched_but_not_allowlisted_is_unknown_rejected():
    c = sync_client(script_transport(health_body("0.5.1")),
                    installed="0.5.1")
    out = c.probe_once(now=0.0)
    assert out.ready is False
    assert out.error_code is ErrorCode.API_CONTRACT_INCOMPATIBLE
    assert classify_version_admission(
        "0.5.1", "0.5.1",
        supported_versions=ALLOW) is VersionAdmission.UNKNOWN


def test_missing_installed_never_ready():
    c = sync_client(script_transport(health_body(ADMITTED)), installed=None)
    out = c.probe_once(now=0.0)
    assert out.ready is False
    assert out.error_code is ErrorCode.API_CONTRACT_INCOMPATIBLE

def test_http_status_error_never_ready():
    c = sync_client(script_transport((503, b'{"version":"x"}')),
                    installed=ADMITTED)
    out = c.probe_once(now=0.0)
    assert out.ready is False
    # Two simultaneous failures (health stage + contract): existing policy
    # assigns no single code, but both failures are recorded.
    assert out.error_code is None
    conds = [f.condition.value for f in out.outcome.failures]
    assert "health" in conds and "api_contract" in conds


def test_http_transport_error_never_ready():
    from castlearq_desktop.core.readiness_client import HttpTransportError
    c = sync_client(script_transport(HttpTransportError("down")),
                    installed=ADMITTED)
    out = c.probe_once(now=0.0)
    assert out.ready is False
    # Transport failure yields no observed status: existing policy returns
    # no single stage code, but the contract failure is still recorded.
    assert out.error_code is None
    conds = [f.condition.value for f in out.outcome.failures]
    assert "health" in conds and "api_contract" in conds


@pytest.mark.parametrize("body", [
    b"not-json",
    b"[1,2]",
    json.dumps({"nope": 1}).encode(),
    json.dumps({"version": 123}).encode(),
    json.dumps({"version": None}).encode(),
])
def test_malformed_health_bodies_rejected(body):
    c = sync_client(script_transport(body), installed=ADMITTED)
    out = c.probe_once(now=0.0)
    assert out.ready is False
    assert out.error_code is ErrorCode.API_CONTRACT_INCOMPATIBLE


def test_failing_extra_endpoints_reject_despite_match():
    c = sync_client(script_transport(health_body(ADMITTED),
                                     models=500, artifacts=200),
                    installed=ADMITTED)
    out = c.probe_once(now=0.0)
    assert out.ready is False


def test_rejected_cases_never_report_ready():
    cases = [
        ("0.5.0", health_body(ADMITTED)),
        ("0.5.1", health_body("0.5.1")),
        (None, health_body(ADMITTED)),
        (ADMITTED, b"not-json"),
    ]
    for installed, body in cases:
        out = sync_client(
            script_transport(body), installed=installed).probe_once(now=0.0)
        assert out.ready is False


def test_timeouts_preserved():
    from castlearq_desktop.core.contracts import Deadline
    c = sync_client(script_transport(health_body(ADMITTED)),
                    installed=ADMITTED)
    deadline = Deadline.starting_now(0.0)
    out = c.wait_until_ready(now=25.0, deadline=deadline)
    assert out.ready is False
    assert out.error_code is ErrorCode.READINESS_TIMEOUT
    assert out.deadline_expired is True


def test_m1_single_resolution_per_attempt_sync_and_once():
    import json
    from castlearq_desktop.core.interpreter_settings import (
        InterpreterSettingsStore)
    from castlearq_desktop.core.startup_coordinator import (
        CoreStartupCoordinator)
    from tests.test_startup_coordinator import (
        AlivePopen, FakeClock, FakeSettings, FakeTimer, PopenFactory,
        TEST_VERSION, _health_body, make_transport, valid_outcome)

    calls = []

    class FakeClock2(FakeClock):
        pass

    settings = InterpreterSettingsStore(FakeSettings())
    settings.save("/abs/env/bin/python")
    coord = CoreStartupCoordinator(
        settings=settings, clock=FakeClock(),
        popen_factory=PopenFactory(AlivePopen()),
        timer_factory=FakeTimer,
        validator=lambda python: valid_outcome(python),
        api_contract=VersionAdmission.ADMITTED,
        installed_version_provider=lambda: calls.append(1) or TEST_VERSION,
        supported_versions=frozenset({TEST_VERSION}))
    coord.launch()
    coord.transport = make_transport({})
    for _ in range(3):
        out = coord.check_readiness_once(now=0.0)
        assert out.ready is True
    # One resolution for the whole attempt despite several polls.
    assert len(calls) == 1
    assert coord._version_cache_valid is True


def test_m1_new_attempt_resolves_again_and_error_stays_fail_closed():
    from castlearq_desktop.core.interpreter_settings import (
        InterpreterSettingsStore)
    from castlearq_desktop.core.startup_coordinator import (
        CoreStartupCoordinator)
    from tests.test_startup_coordinator import (
        AlivePopen, FakeClock, FakeSettings, FakeTimer, PopenFactory,
        TEST_VERSION, make_transport, valid_outcome)

    calls = []
    settings = InterpreterSettingsStore(FakeSettings())
    settings.save("/abs/env/bin/python")

    def _provider():
        calls.append(1)
        if len(calls) == 1:
            return TEST_VERSION
        raise RuntimeError("boom")

    coord = CoreStartupCoordinator(
        settings=settings, clock=FakeClock(),
        popen_factory=PopenFactory(AlivePopen()),
        timer_factory=FakeTimer,
        validator=lambda python: valid_outcome(python),
        api_contract=VersionAdmission.ADMITTED,
        installed_version_provider=_provider,
        supported_versions=frozenset({TEST_VERSION}))
    coord.launch()
    coord.transport = make_transport({})
    assert coord.check_readiness_once(now=0.0).ready is True
    # Second attempt (simulated by re-launch on a fresh manager state):
    # force a refused duplicate is not a new attempt; reset via launch on
    # a new coordinator sharing the provider instead.
    coord2 = CoreStartupCoordinator(
        settings=settings, clock=FakeClock(),
        popen_factory=PopenFactory(AlivePopen()),
        timer_factory=FakeTimer,
        validator=lambda python: valid_outcome(python),
        api_contract=VersionAdmission.ADMITTED,
        installed_version_provider=_provider,
        supported_versions=frozenset({TEST_VERSION}))
    coord2.launch()
    coord2.transport = make_transport({})
    out = coord2.check_readiness_once(now=0.0)
    assert out.ready is False
    assert out.error_code is ErrorCode.API_CONTRACT_INCOMPATIBLE
    assert len(calls) == 2


def test_m2_qt_body_caps_with_doubles():
    import sys
    import types
    from castlearq_desktop.core.version_admission import (
        MAX_HEALTH_BODY_BYTES)

    # Local reply double (no Qt import needed): mirrors the FakeReply
    # protocol used by the Qt tests (error/attribute/header/readAll/abort).
    class _LocalReply:
        def __init__(self, body=b"", declared=None):
            self._body = body
            self._declared_length = declared
            self.read_calls = 0
            self.aborted = False

        def header(self, _kind):
            return self._declared_length

        def readAll(self):
            self.read_calls += 1
            return self._body

        def abort(self):
            self.aborted = True

    # Import the transport with stubbed Qt modules so this test runs
    # without PySide6 (pure doubles, no real Qt).
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_net = types.ModuleType("PySide6.QtNetwork")

    class _Err:
        NoError = 0
        OperationCanceledError = 1
        ProtocolFailure = 2
        ConnectionRefusedError = 3

    class _Attr:
        HttpStatusCodeAttribute = "status"

    class _Req:
        Attribute = _Attr

        class KnownHeaders:
            ContentLengthHeader = "len"

        def __init__(self, *a, **k):
            pass

    class _Reply:
        NetworkError = _Err

    class _Obj:
        def __init__(self, *a, **k):
            pass

        @staticmethod
        def Signal(*a, **k):
            class _S:
                def __init__(self):
                    self.connections = []

                def connect(self, s):
                    self.connections.append(s)

                def emit(self, *a):
                    pass
            return _S()

    qt_core.QObject = _Obj
    qt_core.QUrl = object
    qt_core.Signal = _Obj.Signal
    qt_net.QNetworkAccessManager = object
    qt_net.QNetworkReply = _Reply
    qt_net.QNetworkRequest = _Req
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt_core
    pyside.QtNetwork = qt_net
    saved = {k: sys.modules.get(k) for k in
             ("PySide6", "PySide6.QtCore", "PySide6.QtNetwork")}
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qt_core
    sys.modules["PySide6.QtNetwork"] = qt_net
    try:
        import importlib
        mod = importlib.import_module(
            "castlearq_desktop.core.readiness_qt_transport")
        QtReadinessTransport = mod.QtReadinessTransport
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    # Declared oversize: readAll must be skipped entirely.
    big = _LocalReply(body=b"{}", declared=MAX_HEALTH_BODY_BYTES + 1)
    assert QtReadinessTransport._read_reply_body(big) is None
    assert big.read_calls == 0
    assert big.aborted is True
    # Undeclared oversize: read once, then discarded (fail-closed).
    raw = _LocalReply(body=b"x" * (MAX_HEALTH_BODY_BYTES + 1))
    assert QtReadinessTransport._read_reply_body(raw) is None
    assert raw.read_calls == 1
    # Empty body passes through as empty (admission still rejects it).
    empty = _LocalReply(body=b"")
    assert QtReadinessTransport._read_reply_body(empty) == b""
    assert extract_health_version(b"") == (False, None)
    # Malformed body passes through for the parser to reject.
    bad = _LocalReply(body=b"not-json")
    assert QtReadinessTransport._read_reply_body(bad) == b"not-json"
    assert extract_health_version(b"not-json") == (False, None)


def test_extract_health_version_shapes():
    assert extract_health_version({"version": "0.5.1"}) == (True, "0.5.1")
    assert extract_health_version(b'{"version":"0.5.1"}') == (True, "0.5.1")
    assert extract_health_version('{"version":"0.5.1"}') == (True, "0.5.1")
    assert extract_health_version({"other": 1}) == (False, None)
    assert extract_health_version(b"nope") == (False, None)
    assert evaluate_version_admission(
        ADMITTED, health_body(ADMITTED),
        supported_versions=ALLOW) is VersionAdmission.ADMITTED


def test_not_evaluated_without_provider_blocks_readiness():
    """NOT_EVALUATED with no installed_version_provider stays fail-closed.

    When no provider is configured, _resolve_contract() returns the static
    api_contract (NOT_EVALUATED), which evaluate_readiness() rejects via
    the API_CONTRACT condition. This is the production fallback when the
    async probe path is not available.
    """
    c = ReadinessHttpClient(
        transport=script_transport(health_body(ADMITTED)),
        child_observer=lambda: True,
        api_contract=VersionAdmission.NOT_EVALUATED,
    )
    out = c.probe_once(now=0.0)
    assert out.ready is False
    assert out.error_code is ErrorCode.API_CONTRACT_INCOMPATIBLE
    conds = [f.condition.value for f in out.outcome.failures]
    assert "api_contract" in conds
