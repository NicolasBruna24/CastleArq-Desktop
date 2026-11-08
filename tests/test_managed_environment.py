# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from castlearq_desktop.core.contracts.interpreter import ValidationOutcome
from castlearq_desktop.core.managed_environment import (
    ManagedEnvironmentStatus,
    discover_managed_environment,
    managed_environment_root,
    managed_python_executable,
    resolve_installed_core_version,
)


def test_managed_root_not_repo(tmp_path):
    with patch("castlearq_desktop.core.managed_environment.QStandardPaths") as qs:
        qs.writableLocation.return_value = str(tmp_path)
        root = managed_environment_root()
        assert str(tmp_path) in str(root)
        assert "managed-python" in str(root)


def test_missing_environment(tmp_path):
    with patch("castlearq_desktop.core.managed_environment.managed_environment_root", return_value=tmp_path / "managed-python"):
        res = discover_managed_environment()
        assert res.status == ManagedEnvironmentStatus.MISSING


def test_python_not_executable(tmp_path):
    root = tmp_path / "managed-python"
    root.mkdir()
    with patch("castlearq_desktop.core.managed_environment.managed_environment_root", return_value=root):
        res = discover_managed_environment()
        assert res.status == ManagedEnvironmentStatus.PYTHON_NOT_EXECUTABLE


def test_python_invalid(tmp_path):
    root = tmp_path / "managed-python"
    root.mkdir()
    py = root / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    with patch("castlearq_desktop.core.managed_environment.managed_environment_root", return_value=root), \
         patch("castlearq_desktop.core.managed_environment.validate_python_interpreter") as v:
        v.return_value = ValidationOutcome(code=__import__("castlearq_desktop.core.contracts.errors", fromlist=["ErrorCode"]).ErrorCode.INTERPRETER_INVALID, detail="bad")
        res = discover_managed_environment()
        assert res.status == ManagedEnvironmentStatus.PYTHON_INVALID


def test_core_not_installed(tmp_path):
    root = tmp_path / "managed-python"
    root.mkdir()
    py = root / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    with patch("castlearq_desktop.core.managed_environment.managed_environment_root", return_value=root), \
         patch("castlearq_desktop.core.managed_environment.validate_python_interpreter") as v, \
         patch("castlearq_desktop.core.managed_environment.query_installed_core_version", return_value=None):
        v.return_value = ValidationOutcome(code=None, detail="ok")
        res = discover_managed_environment()
        assert res.status == ManagedEnvironmentStatus.CORE_NOT_INSTALLED


def test_version_incompatible(tmp_path):
    root = tmp_path / "managed-python"
    root.mkdir()
    py = root / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    with patch("castlearq_desktop.core.managed_environment.managed_environment_root", return_value=root), \
         patch("castlearq_desktop.core.managed_environment.validate_python_interpreter") as v, \
         patch("castlearq_desktop.core.managed_environment.query_installed_core_version", return_value="9.9.9"):
        v.return_value = ValidationOutcome(code=None, detail="ok")
        res = discover_managed_environment()
        assert res.status == ManagedEnvironmentStatus.VERSION_INCOMPATIBLE
        assert res.detected_version == "9.9.9"


def test_valid(tmp_path):
    root = tmp_path / "managed-python"
    root.mkdir()
    py = root / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    with patch("castlearq_desktop.core.managed_environment.managed_environment_root", return_value=root), \
         patch("castlearq_desktop.core.managed_environment.validate_python_interpreter") as v, \
         patch("castlearq_desktop.core.managed_environment.query_installed_core_version", return_value="0.6.1"):
        v.return_value = ValidationOutcome(code=None, detail="ok")
        res = discover_managed_environment()
        assert res.status == ManagedEnvironmentStatus.VALID
        assert res.detected_version == "0.6.1"


def test_no_directory_created(tmp_path):
    root = tmp_path / "managed-python"
    with patch("castlearq_desktop.core.managed_environment.managed_environment_root", return_value=root):
        discover_managed_environment()
def _managed_result(status, version=None, python_path="/managed/python"):
    from types import SimpleNamespace
    return SimpleNamespace(status=status, detected_version=version, python_path=python_path)


def test_resolve_valid_version():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.VALID, "0.6.0")
    settings = object()
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings) == "0.6.0"


def test_resolve_valid_none_no_fallback():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.VALID, None)
    load = lambda: "/manual/python"
    settings = type("S", (), {"load": staticmethod(load)})()
    q = lambda p: "0.6.0"
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) is None


def test_resolve_missing_fallback():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.MISSING, None)
    load = lambda: "/manual/python"
    settings = type("S", (), {"load": staticmethod(load)})()
    q = lambda p: "0.6.0"
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) == "0.6.0"


def test_resolve_core_not_installed_fallback():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.CORE_NOT_INSTALLED, None)
    load = lambda: "/manual/python"
    settings = type("S", (), {"load": staticmethod(load)})()
    q = lambda p: "0.6.0"
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) == "0.6.0"


def test_resolve_python_not_executable_no_fallback():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.PYTHON_NOT_EXECUTABLE, None)
    load = lambda: "/manual/python"
    settings = type("S", (), {"load": staticmethod(load)})()
    q = lambda p: "0.6.0"
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) is None


def test_resolve_python_invalid_no_fallback():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.PYTHON_INVALID, None)
    load = lambda: "/manual/python"
    settings = type("S", (), {"load": staticmethod(load)})()
    q = lambda p: "0.6.0"
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) is None


def test_resolve_version_incompatible_no_fallback():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.VERSION_INCOMPATIBLE, "9.9.9")
    load = lambda: "/manual/python"
    settings = type("S", (), {"load": staticmethod(load)})()
    q = lambda p: "0.6.0"
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) is None


def test_resolve_no_settings():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.MISSING, None)
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=None) is None


def test_resolve_manual_invalid():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.MISSING, None)
    load = lambda: ""
    settings = type("S", (), {"load": staticmethod(load)})()
    q = lambda p: "0.6.0"
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) is None


def test_resolve_provider_exception():
    def provider():
        raise RuntimeError("boom")
    settings = None
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings) is None


def test_resolve_version_query_exception():
    provider = lambda: _managed_result(ManagedEnvironmentStatus.MISSING, None)
    load = lambda: "/manual/python"
    settings = type("S", (), {"load": staticmethod(load)})()
    def q(p):
        raise RuntimeError("boom")
    assert resolve_installed_core_version(managed_environment_provider=provider, settings=settings, version_query=q) is None


def test_explicit_none_does_not_discover_again():
    def provider():
        raise AssertionError("provider must not run for supplied None")
    assert resolve_installed_core_version(
        managed_environment_provider=provider, managed_result=None) is None


def test_explicit_managed_result_reused_without_provider():
    managed = _managed_result(ManagedEnvironmentStatus.VALID, "0.6.2")
    assert resolve_installed_core_version(
        managed_environment_provider=lambda: (_ for _ in ()).throw(
            AssertionError("unexpected discovery")),
        managed_result=managed) == "0.6.2"


def test_default_provider_behavior_remains_available(monkeypatch):
    managed = _managed_result(ManagedEnvironmentStatus.VALID, "0.6.3")
    monkeypatch.setattr(
        "castlearq_desktop.core.managed_environment.discover_managed_environment",
        lambda: managed)
    assert resolve_installed_core_version() == "0.6.3"
