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

"""Tests for CoreStartupCoordinator.connect_existing().

All transports are simulated: nothing touches Core, no process is started and
no persistent setting is written.
"""

from __future__ import annotations

import json

import pytest
import unittest.mock as mock

from castlearq_desktop.core.contracts import ErrorCode
from castlearq_desktop.core.contracts import VersionAdmission
from castlearq_desktop.core.readiness_client import HttpProbeResponse
from castlearq_desktop.core.startup_coordinator import (
    ConnectionAdmission,
    ConnectionAdmissionStatus,
    CoreStartupCoordinator,
)
from castlearq_desktop.core.lifecycle_manager import LifecycleManager, LifecycleState


class FakeSettings:
    def __init__(self):
        self.data = {}

    def value(self, key, defaultValue=None):
        return self.data.get(key, defaultValue)

    def setValue(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)

    def sync(self):
        pass


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def monotonic(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class FakePopen:
    def __init__(self, exit_code=None, raises=None):
        self._exit_code = exit_code
        self._raises = raises or {}
        self.terminates = 0
        self.kills = 0

    def poll(self):
        action = self._raises.get("poll")
        if action is not None:
            raise action
        return self._exit_code

    def is_alive_probe(self):
        return self._exit_code is None

    def terminate(self):
        self.terminates += 1

    def kill(self):
        self.kills += 1


class _CountingAdapter:
    """Adapter that counts spawns (no behaviour change)."""

    def __init__(self, popen_factory=None):
        self._popen_factory = popen_factory
        self._owner = None

    def spawn(self, argv, *, shell):
        if self._owner is not None:
            self._owner.spawn_calls += 1
        return None


@pytest.fixture
def coord_factory():
    def _make(*, installed_version_provider=None, supported_versions=None, transport=None):
        owner = type("DummyOwner", (), {"spawn_calls": 0})()
        adapter = _CountingAdapter()
        adapter._owner = owner
        settings = FakeSettings()
        clock = FakeClock()
        coordinator = CoreStartupCoordinator(
            settings=settings,
            clock=clock,
            popen_factory=lambda *a, **k: FakePopen(),
            api_contract=VersionAdmission.NOT_EVALUATED,
            installed_version_provider=installed_version_provider,
            supported_versions=supported_versions or frozenset(),
        )
        coordinator.transport = transport
        coordinator.manager = LifecycleManager(adapter=adapter, clock=clock)
        # The coordinator is deliberately NOT started here. ``connect_existing``
        # must not launch a process; any spawn would come from the manager setup
        # above, not from the probe itself.
        coordinator.manager.state = LifecycleState.NOT_STARTED
        return coordinator, owner

    return _make


def health_body(version):
    return json.dumps({"version": version}).encode()


def script_transport(health, *, models=200, artifacts=200):
    def _transport(path, *, timeout):
        if path == "/health":
            return HttpProbeResponse(status=200, body=health)
        if path == "/v1/models":
            return HttpProbeResponse(status=models, body=b"{}")
        return HttpProbeResponse(status=artifacts, body=b"{}")

    return _transport


# -- 1. Versión admitida y conexión correcta ----------------------------------
def test_admitted_version_and_connection_ok(coord_factory):
    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(health_body("0.5.1")),
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.ADMITTED
    assert result.code is None
    assert result.version_admission is VersionAdmission.ADMITTED
    assert result.version == "0.5.1"
    assert result.detail == "Core connection admitted and ready."


# -- 2. Versión desconocida con formato válido no es admisión ------------------
def test_unknown_version_with_valid_format_is_not_admitted(coord_factory):
    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.6.0",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(health_body("0.6.0")),
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.INCOMPATIBLE_VERSION
    assert result.code is ErrorCode.API_CONTRACT_INCOMPATIBLE
    assert result.version_admission is VersionAdmission.UNKNOWN
    assert result.version == "0.6.0"


# -- 3. Versión malformada ----------------------------------------------------
def test_malformed_version_is_not_admitted(coord_factory):
    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(b'{"version": "0.5.1!"}'),
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.MALFORMED_VERSION
    assert result.code is ErrorCode.API_CONTRACT_INCOMPATIBLE
    assert result.version_admission is VersionAdmission.MALFORMED
    assert result.version == "0.5.1!"


# -- 4. Versión ausente -------------------------------------------------------
def test_missing_version_is_not_confirmed_admission(coord_factory):
    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(b"{}", models=200),
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.MISSING_VERSION
    assert result.code is None
    assert result.version_admission is VersionAdmission.MISSING


@pytest.mark.parametrize(
    "non_2xx_path,expected_status,expected_code",
    [
        ("/v1/models", 500, ErrorCode.API_PROBE_FAILED),
        ("/health", 503, ErrorCode.HEALTH_CHECK_FAILED),
    ],
)
def test_http_error_on_required_endpoint(coord_factory, non_2xx_path, expected_status, expected_code):
    def failing_transport(path, *, timeout):
        if non_2xx_path == "/health":
            # models respond; health endpoint fails
            if path == "/v1/models":
                return HttpProbeResponse(status=200, body=b"{}")
            if path == "/health":
                return HttpProbeResponse(status=503, body=b"{}")
            return HttpProbeResponse(status=200, body=b"{}")
        # models endpoint fails; health endpoint responds
        if path == "/v1/models":
            return HttpProbeResponse(status=500, body=b"{}")
        if path == "/health":
            return HttpProbeResponse(status=200, body=b"{}")
        return HttpProbeResponse(status=200, body=b"{}")

    coordinator, _ = coord_factory(
        supported_versions=frozenset({"0.5.1"}),
        transport=failing_transport,
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.HTTP_ERROR
    assert result.code is expected_code


def test_connection_refused_does_not_admit(coord_factory):
    def refused_transport(path, *, timeout):
        raise ConnectionRefusedError("connection refused")

    coordinator, _ = coord_factory(transport=refused_transport)
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.CONNECTION_REFUSED
    assert result.code is None


def test_timeout_is_reported_as_timeout(coord_factory):
    # A single request exceeding its effective timeout, raised as in the
    # production urllib_transport path (HttpRequestTimeout).
    from castlearq_desktop.core.readiness_client import HttpRequestTimeout

    def timeout_transport(path, *, timeout):
        raise HttpRequestTimeout(f"timeout on {path}")

    coordinator, _ = coord_factory(transport=timeout_transport)
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.TIMEOUT
    assert result.code is None


def test_invalid_json_body_is_invalid_response(coord_factory):
    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(b"not json at all"),
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.INVALID_RESPONSE
    assert result.code is None
    assert result.version_admission is VersionAdmission.MALFORMED


def test_artifacts_failure_does_not_invalidate_admitted_connection(coord_factory):
    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(health_body("0.5.1"), artifacts=500),
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.ADMITTED
    assert result.code is None
    assert result.version_admission is VersionAdmission.ADMITTED


def test_default_url_is_used_when_no_base_url_given(coord_factory):
    # When no base_url is supplied, connect_existing must resolve the default
    # loopback base URL (CORE_HOST:8000) and probe the standard endpoints.
    probed = []

    def recording_transport(path, *, timeout):
        probed.append(path)
        return HttpProbeResponse(status=200, body=health_body("0.5.1"))

    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=recording_transport,
    )
    result = coordinator.connect_existing()
    assert result.status is ConnectionAdmissionStatus.ADMITTED
    assert sorted(probed) == ["/health", "/v1/artifacts", "/v1/models"]


def test_custom_base_url_is_used_when_supplied(coord_factory):
    def recording_transport(path, *, timeout):
        return HttpProbeResponse(status=200, body=health_body("0.5.1"))

    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=recording_transport,
    )
    result = coordinator.connect_existing(base_url="http://127.0.0.1:9000")
    assert result.status is ConnectionAdmissionStatus.ADMITTED
    assert coordinator.transport is not None


def test_no_process_is_started(coord_factory):
    coordinator, owner = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(health_body("0.5.1")),
    )
    coordinator.connect_existing()
    assert owner.spawn_calls == 0


def test_no_persistent_config_is_written(coord_factory):
    coordinator, _ = coord_factory(
        installed_version_provider=lambda: "0.5.1",
        supported_versions=frozenset({"0.5.1"}),
        transport=script_transport(health_body("0.5.1")),
    )
    coordinator.connect_existing()
    assert coordinator.settings.data == {}


class TestDecAHelpers:
    """Pure DEC-A helpers are exercised without a coordinator."""

    def test_classify_version_admission_reuses_central_allowlist(self):
        from castlearq_desktop.core.version_admission import (
            classify_version_admission,
            SUPPORTED_CORE_VERSIONS,
        )

        admitted = classify_version_admission(
            "0.5.1", "0.5.1", supported_versions=frozenset({"0.5.1"})
        )
        assert admitted is VersionAdmission.ADMITTED

        not_admitted = classify_version_admission(
            "0.6.0", "0.6.0", supported_versions=frozenset({"0.5.1"})
        )
        assert not_admitted is VersionAdmission.UNKNOWN

        incompatible = classify_version_admission(
            "0.5.1", "0.6.0", supported_versions=frozenset({"0.5.1"})
        )
        assert incompatible is VersionAdmission.INCOMPATIBLE

    def test_evaluate_version_admission(self):
        from castlearq_desktop.core.version_admission import evaluate_version_admission

        admission = evaluate_version_admission(
            "0.5.1",
            b'{"version": "0.5.1"}',
            supported_versions=frozenset({"0.5.1"}),
        )
        assert admission is VersionAdmission.ADMITTED
