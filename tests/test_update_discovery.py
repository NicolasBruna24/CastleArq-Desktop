# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Stage 2 tests: deterministic Core Update Discovery v1 service tests.

No real network access: PyPI responses are faked (in-memory opener/response
fakes) or served by a controlled loopback HTTP server. The real production
transport function (``fetch_pypi_project_body``) is exercised against those
controlled inputs.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from castlearq_desktop.core.update_discovery import (
    MAX_UPDATE_CHECK_TIMEOUT_SECONDS,
    MAX_UPDATE_RESPONSE_BYTES,
    PYPI_HOST,
    PYPI_PROJECT_JSON_URL,
    _PinnedHostRedirectHandler,
    CoreUpdateDiscoveryState,
    UpdateDiscoveryError,
    UpdateDiscoveryFailureReason,
    classify_update_discovery,
    discover_core_update,
    fetch_pypi_project_body,
    resolve_installed_version,
    select_latest_stable_version,
)
from castlearq_desktop.core.version_admission import SUPPORTED_CORE_VERSIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_body(releases, info_version="0.5.1"):
    return json.dumps(
        {"info": {"version": info_version}, "releases": releases}
    ).encode("utf-8")


class FakeHeaders(dict):
    def get(self, key, default=None):  # noqa: D102 - dict-compatible
        return super().get(key, default)


class FakeResponse:
    """In-memory stand-in for the urllib response object."""

    def __init__(self, body=b"", status=200, headers=None, on_read=None,
                 url=PYPI_PROJECT_JSON_URL):
        self._body = bytes(body)
        self.status = status
        self.headers = FakeHeaders(headers or {})
        self._pos = 0
        self._on_read = on_read
        self._url = url
        self.closed = False

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status

    def read(self, size=-1):
        if self._on_read is not None:
            self._on_read()
        if size is None or size < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


def fake_opener_factory(response=None, exc=None, record=None):
    """Build an opener fake; records (request, timeout) calls."""

    class _FakeOpener:
        def open(self, request, timeout=None):
            if record is not None:
                record.append((request, timeout))
            if exc is not None:
                raise exc
            return response

    return _FakeOpener()


def fake_execute_version(version):
    def _execute(cmd, **kwargs):
        assert kwargs.get("shell") is False
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"distribution_version": version}),
        )

    return _execute


def raising_execute(exc):
    def _execute(cmd, **kwargs):
        raise exc

    return _execute


ADMITTED = frozenset({"0.5.1"})
NOT_ADMITTED_ALLOW = frozenset({"9.9.9-allow-test"})

# ---------------------------------------------------------------------------
# 3. PEP 440 selection and comparison
# ---------------------------------------------------------------------------

def test_selection_picks_newest_stable():
    body = make_body(
        {
            "0.5.1": [{"filename": "a.whl", "yanked": False}],
            "0.6.0": [{"filename": "b.whl", "yanked": False}],
            "0.4.0": [{"filename": "c.whl", "yanked": False}],
        },
        info_version="0.6.0",
    )
    latest, ctx = select_latest_stable_version(body)
    assert latest == "0.6.0"
    assert ctx["candidate_count"] >= 1
    assert ctx["info_version_matches"] is True


def test_selection_ignores_key_order_independence():
    body = make_body(
        {
            "0.10.0": [{"filename": "a.whl"}],
            "0.9.0": [{"filename": "b.whl"}],
            "0.5.1": [{"filename": "c.whl"}],
        },
        info_version="0.10.0",
    )
    latest, _ = select_latest_stable_version(body)
    assert latest == "0.10.0"


@pytest.mark.parametrize("key", ["0.6.0a1", "0.6.0b2", "0.6.0rc1", "0.6.0.dev1"])
def test_selection_excludes_prereleases(key):
    body = make_body(
        {"0.5.1": [{"filename": "a.whl"}], key: [{"filename": "b.whl"}]},
        info_version="0.5.1",
    )
    latest, ctx = select_latest_stable_version(body)
    assert latest == "0.5.1"
    assert ctx["excluded_prereleases"] == 1


@pytest.mark.parametrize("key", ["!!!bad!!!", "", "1.0..0.."])
def test_selection_discards_invalid_keys(key):
    body = make_body(
        {"0.5.1": [{"filename": "a.whl"}], key: [{"filename": "b.whl"}]},
        info_version="0.5.1",
    )
    latest, ctx = select_latest_stable_version(body)
    assert latest == "0.5.1"
    assert len(ctx["invalid_version_keys"]) >= 1


def test_selection_rejects_fileless_and_yanked():
    body = make_body(
        {
            "0.5.1": [{"filename": "a.whl"}],
            "0.7.0": [],
            "0.8.0": [{"filename": "b.whl", "yanked": True}],
            "0.6.0": [{"filename": "c.whl"}],
        },
        info_version="0.7.0",
    )
    latest, ctx = select_latest_stable_version(body)
    assert latest == "0.6.0"
    assert ctx["excluded_unusable"] == 2


def test_selection_accepts_mixed_yanked_release():
    body = make_body(
        {
            "0.5.1": [{"filename": "a.whl"}],
            "0.9.0": [
                {"filename": "old.whl", "yanked": True},
                {"filename": "new.whl", "yanked": False},
            ],
        },
        info_version="0.9.0",
    )
    latest, _ = select_latest_stable_version(body)
    assert latest == "0.9.0"


def test_selection_handles_unexpected_file_entry_types():
    body = make_body(
        {
            "0.5.1": [{"filename": "a.whl"}],
            "0.9.0": ["not-a-dict", None, 42],
            "0.9.1": [{"filename": "b.whl"}],
        },
        info_version="0.9.1",
    )
    latest, _ = select_latest_stable_version(body)
    assert latest == "0.9.1"


def test_selection_info_crosscheck_and_no_stable():
    body = make_body({"0.5.1": [{"filename": "a.whl"}]}, info_version="9.9.9")
    latest, ctx = select_latest_stable_version(body)
    assert latest == "0.5.1"
    assert ctx["info_version_matches"] is False

    empty = make_body(
        {"0.6.0a1": [{"filename": "a.whl"}], "0.7.0": []},
        info_version="0.6.0a1",
    )
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        select_latest_stable_version(empty)
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.NO_STABLE_VERSION


def test_selection_malformed_metadata():
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        select_latest_stable_version(b"not json{{")
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.INVALID_JSON
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        select_latest_stable_version('{"info": {}}'.encode())
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.INCOMPLETE_METADATA


def test_admission_exact_match_only():
    assert classify_update_discovery(
        installed_version="0.5.1", latest_stable="0.5.1", admitted_versions=ADMITTED
    ) is CoreUpdateDiscoveryState.LATEST_INSTALLED
    # No ranges/partials: "0.5.10" is unequal to installed "0.5.1",
    # so the installed version is ahead of latest rather than current.
    assert classify_update_discovery(
        installed_version="0.5.10", latest_stable="0.5.1", admitted_versions=ADMITTED
    ) is CoreUpdateDiscoveryState.INSTALLED_AHEAD


# ---------------------------------------------------------------------------
# 4. HTTP transport (real production functions, faked inputs)
# ---------------------------------------------------------------------------

def test_fetch_success_records_single_https_request():
    body = b'{"info": {"version": "0.6.0"}, "releases": {"0.6.0": []}}'
    resp = FakeResponse(body, headers={"Content-Length": str(len(body))})
    record = []
    out = fetch_pypi_project_body(
        deadline=time.monotonic() + 10.0, opener=fake_opener_factory(resp, record=record)
    )
    assert out == body
    assert len(record) == 1
    request, _ = record[0]
    assert request.full_url == PYPI_PROJECT_JSON_URL
    assert request.full_url.startswith("https://")


def test_fetch_http_non_success():
    err = urllib.error.HTTPError(PYPI_PROJECT_JSON_URL, 404, "NF", None, None)
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=time.monotonic() + 10.0, opener=fake_opener_factory(exc=err)
        )
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.HTTP_NON_SUCCESS
    assert excinfo.value.technical_context["http_status"] == 404


def test_fetch_connection_failure_and_no_retry():
    record = []
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=time.monotonic() + 10.0,
            opener=fake_opener_factory(exc=OSError("down"), record=record),
        )
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.NETWORK_ERROR
    assert len(record) == 1


def test_fetch_rejects_oversized_content_length_before_reading():
    reads = []
    resp = FakeResponse(
        b"x", headers={"Content-Length": str(MAX_UPDATE_RESPONSE_BYTES + 1)},
        on_read=lambda: reads.append(1),
    )
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=time.monotonic() + 10.0, opener=fake_opener_factory(resp)
        )
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.RESPONSE_TOO_LARGE
    assert reads == []


# ---------------------------------------------------------------------------
# 4b. Streaming caps, redirects, slow reads (real production transport)
# ---------------------------------------------------------------------------

class ChunkedResponse(FakeResponse):
    def __init__(self, chunks, **kwargs):
        super().__init__(b"", **kwargs)
        self._chunks = [bytes(c) for c in chunks]

    def read(self, size=-1):
        if self._on_read is not None:
            self._on_read()
        if not self._chunks:
            return b""
        head = self._chunks.pop(0)
        if size is not None and 0 <= size < len(head):
            self._chunks.insert(0, head[size:])
            return head[:size]
        return head


class ScriptedClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_stream_cap_with_misleading_content_length():
    body = b"y" * (MAX_UPDATE_RESPONSE_BYTES + 16)
    resp = ChunkedResponse(
        [body[i : i + 4096] for i in range(0, len(body), 4096)],
        headers={"Content-Length": "10"},
    )
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=time.monotonic() + 10.0, opener=fake_opener_factory(resp)
        )
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.RESPONSE_TOO_LARGE


def test_stream_at_exact_limit_succeeds():
    body = b"z" * MAX_UPDATE_RESPONSE_BYTES
    resp = ChunkedResponse([body[i : i + 65536] for i in range(0, len(body), 65536)])
    out = fetch_pypi_project_body(
        deadline=time.monotonic() + 60.0, opener=fake_opener_factory(resp)
    )
    assert out == body

def test_same_host_redirect_allowed_offhost_refused():
    handler = _PinnedHostRedirectHandler()
    req = urllib.request.Request(PYPI_PROJECT_JSON_URL)
    allowed = handler.redirect_request(
        req, None, 302, "moved", {}, "https://pypi.org/other")
    assert allowed is not None  # exact host, https: allowed
    # Exact-host pinning: subdomains, suffix lookalikes, other hosts,
    # and scheme downgrades are all refused before any body is requested.
    for bad in (
        "https://sub.pypi.org/other",
        "https://pypi.org.evil.example/other",
        "https://files.example.com/other",
        "https://evil-pypi.org/other",
        "http://pypi.org/other",
    ):
        with pytest.raises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, "moved", {}, bad)


def test_final_url_defense_check():
    resp = FakeResponse(b"{}", url="https://files.example.com/castlearq")
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=time.monotonic() + 10.0, opener=fake_opener_factory(resp))
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.NETWORK_ERROR
    assert excinfo.value.technical_context["stage"] == "final_url_check"


def test_timeout_before_request_makes_no_call():
    record = []
    clock = ScriptedClock()
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=clock() - 0.1, now=clock,
            opener=fake_opener_factory(FakeResponse(b"{}"), record=record))
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.TIMEOUT
    assert record == []


def test_timeout_between_chunks_and_no_second_request():
    resp = ChunkedResponse([b'{"a": 1}', b'{"b": 2}'])
    record = []
    clock = ScriptedClock()
    calls = {"reads": 0}
    orig_read = resp.read

    def slow_read(size=-1):
        calls["reads"] += 1
        if calls["reads"] > 1:
            clock.advance(30.0)
        return orig_read(size)

    resp.read = slow_read
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=clock() + 10.0, now=clock,
            opener=fake_opener_factory(resp, record=record))
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.TIMEOUT
    assert len(record) == 1


def test_read_oserror_maps_to_network_error():
    class BrokenRead(FakeResponse):
        def read(self, size=-1):
            raise OSError("reset")

    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=time.monotonic() + 10.0,
            opener=fake_opener_factory(BrokenRead(b"partial")))
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.NETWORK_ERROR


def test_production_redirect_handler_against_loopback_server():
    """Real sockets + real handler: off-host redirect refused before body fetch."""
    import http.server
    import threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "https://files.example.com/evil.pkg")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import urllib.request as urlrequest

        opener = urlrequest.build_opener(_PinnedHostRedirectHandler())
        host, port = server.server_address
        with pytest.raises(urllib.error.URLError):
            opener.open(
                urlrequest.Request(f"http://{host}:{port}/castlearq"), timeout=5)
        # The redirect target was never requested: exactly one hit, the 302.
        assert hits == ["/castlearq"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# 5-6. Orchestrator results, errors, architectural isolation
# ---------------------------------------------------------------------------

def _fetch_ok(version, files=None):
    body = make_body(
        {version: files if files is not None else [{"filename": "x.whl"}]},
        info_version=version,
    )

    def _fetch(deadline):
        return body

    return _fetch


def _probe_ok(version="0.5.1"):
    def _execute(cmd, **kwargs):
        assert isinstance(cmd[0], str) and cmd[0].startswith("/")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"distribution_version": version}),
            stderr="",
        )

    return _execute


def test_result_success_installed_current():
    res = discover_core_update(
        python_path="/explicit/python",
        admitted_versions=ADMITTED,
        fetch=_fetch_ok("0.5.1"),
        version_execute=_probe_ok("0.5.1"),
    )
    assert res.state is CoreUpdateDiscoveryState.LATEST_INSTALLED
    assert res.installed_version == "0.5.1"
    assert res.latest_stable == "0.5.1"
    assert res.admitted_versions == ADMITTED
    assert res.search_failure is None and res.installed_failure is None


def test_result_newer_published_not_admitted():
    res = discover_core_update(
        python_path="/explicit/python",
        admitted_versions=ADMITTED,
        fetch=_fetch_ok("0.6.0"),
        version_execute=_probe_ok("0.5.1"),
    )
    assert res.state is CoreUpdateDiscoveryState.NEWER_STABLE_NOT_ADMITTED
    assert res.latest_stable == "0.6.0"
    assert "0.6.0" not in res.admitted_versions


def test_result_newer_published_admitted_snapshot():
    res = discover_core_update(
        python_path="/explicit/python",
        admitted_versions=frozenset({"0.6.0"}),
        fetch=_fetch_ok("0.6.0"),
        version_execute=_probe_ok("0.5.1"),
    )
    assert res.state is CoreUpdateDiscoveryState.NEWER_STABLE_ADMITTED


def test_result_no_interpreter_still_reports_published():
    res = discover_core_update(
        python_path=None, admitted_versions=ADMITTED, fetch=_fetch_ok("0.6.0"))
    assert res.state is CoreUpdateDiscoveryState.INSTALLED_UNKNOWN
    assert res.installed_version is None
    assert res.latest_stable == "0.6.0"
    assert res.installed_failure is not None
    assert res.installed_failure.reason is UpdateDiscoveryFailureReason.INSTALLED_UNKNOWN
    assert res.search_failure is None


def test_result_remote_failure_preserves_installed():
    def _boom(deadline):
        raise UpdateDiscoveryError(UpdateDiscoveryFailureReason.NETWORK_ERROR)

    res = discover_core_update(
        python_path="/explicit/python",
        admitted_versions=ADMITTED,
        fetch=_boom,
        version_execute=_probe_ok("0.5.1"),
    )
    assert res.state is CoreUpdateDiscoveryState.SEARCH_FAILED
    assert res.installed_version == "0.5.1"
    assert res.latest_stable is None
    assert res.search_failure is not None


def test_result_both_failures_coexist():
    def _boom(deadline):
        raise UpdateDiscoveryError(UpdateDiscoveryFailureReason.TIMEOUT)

    res = discover_core_update(
        python_path=None,
        admitted_versions=ADMITTED,
        fetch=_boom,
        version_execute=raising_execute(RuntimeError("probe down")),
    )
    assert res.state is CoreUpdateDiscoveryState.SEARCH_FAILED
    assert res.search_failure is not None
    assert res.installed_failure is not None
    assert res.installed_failure.reason is UpdateDiscoveryFailureReason.INSTALLED_UNKNOWN


# ---------------------------------------------------------------------------
# 5b. Fine-grained error mapping, timeout review, diagnostics
# ---------------------------------------------------------------------------

def test_invalid_utf8_body_maps_to_invalid_json():
    body = b'{"info": {"version": "\xff\xfe"}, "releases": {}}'
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        select_latest_stable_version(body)
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.INVALID_JSON
    diag = excinfo.value.diagnostics()
    assert diag["reason"] == UpdateDiscoveryFailureReason.INVALID_JSON.value
    assert "traceback" not in json.dumps(diag).lower()


def test_non_object_top_level_maps_to_incomplete_metadata():
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        select_latest_stable_version(b"[1, 2, 3]")
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.INCOMPLETE_METADATA


def test_wrong_typed_releases_maps_to_incomplete_metadata():
    with pytest.raises(UpdateDiscoveryError) as excinfo:
        select_latest_stable_version(
            json.dumps({"info": {"version": "0.5.1"}, "releases": [1]}).encode()
        )
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.INCOMPLETE_METADATA


def test_probe_invalid_output_maps_to_installed_unknown():
    def _bad_execute(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="garbage-no-json\n", stderr="")

    installed, failure = resolve_installed_version("/explicit/python", execute=_bad_execute)
    assert installed is None
    assert failure is not None
    assert failure.reason is UpdateDiscoveryFailureReason.INSTALLED_UNKNOWN


def test_slow_drip_real_clock_times_out_without_restart():
    chunks = [b'{"info": {"version": "0.6.0"}, '] + [b'"releases": {"0.6.0": []}}']
    delays = [0.0, 0.35]  # real sleeps; total budget is 0.3 s

    class SlowDrip(ChunkedResponse):
        def __init__(self):
            super().__init__(chunks)
            self.calls = 0

        def read(self, size=-1):
            time.sleep(delays[min(self.calls, len(delays) - 1)])
            self.calls += 1
            return super().read(size)

    with pytest.raises(UpdateDiscoveryError) as excinfo:
        fetch_pypi_project_body(
            deadline=time.monotonic() + 0.3,
            opener=fake_opener_factory(SlowDrip()),
        )
    assert excinfo.value.reason is UpdateDiscoveryFailureReason.TIMEOUT


def test_orchestrator_installed_ahead_and_remote_installed_coexist():
    res = discover_core_update(
        python_path="/explicit/python",
        admitted_versions=ADMITTED,
        fetch=_fetch_ok("0.5.1"),
        version_execute=_probe_ok("0.7.0"),
    )
    assert res.state is CoreUpdateDiscoveryState.INSTALLED_AHEAD
    assert res.installed_version == "0.7.0"
    assert res.latest_stable == "0.5.1"


def test_orchestrator_invalid_probe_output_keeps_published():
    res = discover_core_update(
        python_path="/explicit/python",
        admitted_versions=ADMITTED,
        fetch=_fetch_ok("0.6.0"),
        version_execute=raising_execute(RuntimeError("probe down")),
    )
    assert res.state is CoreUpdateDiscoveryState.INSTALLED_UNKNOWN
    assert res.latest_stable == "0.6.0"
    assert res.installed_failure is not None
    assert res.search_failure is None


def test_diagnostics_carry_no_traceback():
    try:
        raise RuntimeError("hidden internals")
    except RuntimeError as exc:
        failure = UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.NETWORK_ERROR,
            technical_context={"stage": "open"},
        )
        failure.__cause__ = exc
    diag = failure.diagnostics()
    assert "traceback" not in json.dumps(diag).lower()
    assert diag["cause_type"] == "RuntimeError"
    # The actionable user message carries no internal detail.
    assert "hidden internals" not in str(failure.user_message)


# ---------------------------------------------------------------------------
# 6. Architectural isolation
# ---------------------------------------------------------------------------

def test_allowlist_matches_admitted_set_and_result_snapshot_is_defensive():
    assert SUPPORTED_CORE_VERSIONS == frozenset({"0.5.1", "0.6.0", "0.6.1"})
    res = discover_core_update(
        python_path=None,
        fetch=_fetch_ok("0.5.1"),
    )
    assert res.admitted_versions == frozenset({"0.5.1", "0.6.0", "0.6.1"})
    assert isinstance(res.admitted_versions, frozenset)
    assert SUPPORTED_CORE_VERSIONS == frozenset({"0.5.1", "0.6.0", "0.6.1"})


def test_discovery_does_not_touch_global_error_vocabulary():
    import castlearq_desktop.core.contracts.errors as errors_mod

    before = frozenset(errors_mod.ErrorCode)
    res = discover_core_update(python_path=None, fetch=_fetch_ok("0.5.1"))
    assert res.state is CoreUpdateDiscoveryState.INSTALLED_UNKNOWN
    assert frozenset(errors_mod.ErrorCode) == before


def test_no_real_network_in_deterministic_suite():
    import socket as socket_mod

    calls = []
    real_create = socket_mod.create_connection

    def _guard(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("real network access is forbidden in this suite")

    socket_mod.create_connection = _guard
    try:
        res = discover_core_update(
            python_path="/explicit/python",
            admitted_versions=ADMITTED,
            fetch=_fetch_ok("0.5.1"),
            version_execute=_probe_ok("0.5.1"),
        )
    finally:
        socket_mod.create_connection = real_create
    assert calls == []
    assert res.state is CoreUpdateDiscoveryState.LATEST_INSTALLED
