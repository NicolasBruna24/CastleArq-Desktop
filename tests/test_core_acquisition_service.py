from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import urllib.error

from castlearq_desktop.core.core_acquisition_service import (
    EXPECTED_SHA256,
    EXPECTED_WHEEL,
    acquire_core_0_6_0,
    AcquisitionErrorKind,
)


def test_version_not_admitted():
    r = acquire_core_0_6_0(version="0.5.1")
    assert not r.verified
    assert r.error_kind == AcquisitionErrorKind.VERSION_NOT_ADMITTED


def test_hash_correct(tmp_path):
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    import castlearq_desktop.core.core_acquisition_service as m
    orig = m.EXPECTED_SHA256
    m.EXPECTED_SHA256 = h
    try:
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = data
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=fake_resp):
            r = acquire_core_0_6_0(destination_dir=tmp_path)
        assert r.verified
        assert r.filename == EXPECTED_WHEEL
        assert (tmp_path / EXPECTED_WHEEL).exists()
    finally:
        m.EXPECTED_SHA256 = orig


def test_hash_incorrect():
    data = b"hello"
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = data
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake_resp):
        r = acquire_core_0_6_0()
    assert not r.verified
    assert r.error_kind == AcquisitionErrorKind.HASH_MISMATCH


def test_http_failed():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        r = acquire_core_0_6_0()
    assert not r.verified
    assert r.error_kind == AcquisitionErrorKind.NETWORK


def test_asset_absent():
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None)):
        r = acquire_core_0_6_0()
    assert not r.verified
    assert r.error_kind == AcquisitionErrorKind.ASSET_MISSING


def test_incomplete_download():
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = b""
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake_resp):
        r = acquire_core_0_6_0()
    assert not r.verified
    assert r.error_kind == AcquisitionErrorKind.INCOMPLETE_DOWNLOAD
