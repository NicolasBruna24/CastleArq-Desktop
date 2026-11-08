# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Independent acquisition and verification of the official CastleArq Core 0.6.0 wheel."""

from __future__ import annotations

import hashlib
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

__all__ = ["AcquisitionErrorKind", "CoreAcquisitionResult", "acquire_core_0_6_0"]

ADMITTED_VERSION = "0.6.0"
EXPECTED_WHEEL = "castlearq-0.6.0-py3-none-any.whl"
EXPECTED_SHA256 = "cc4bee15656aa5a6da4a81f4b2bce4d40c5a2e29cb42fc5d04dc024ad19c6757"
OFFICIAL_URL = "https://github.com/NicolasBruna24/CastleArq/releases/download/v0.6.0/castlearq-0.6.0-py3-none-any.whl"


class AcquisitionErrorKind(str, Enum):
    VERSION_NOT_ADMITTED = "version_not_admitted"
    NETWORK = "network"
    RELEASE_NOT_FOUND = "release_not_found"
    ASSET_MISSING = "asset_missing"
    INCOMPLETE_DOWNLOAD = "incomplete_download"
    HASH_MISMATCH = "hash_mismatch"


@dataclass(frozen=True)
class CoreAcquisitionResult:
    version: str
    filename: str
    path: Path
    origin: str
    sha256: str
    verified: bool
    error: Optional[str] = None
    error_kind: Optional[AcquisitionErrorKind] = None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def acquire_core_0_6_0(version: str = ADMITTED_VERSION, destination_dir: Optional[Path] = None) -> CoreAcquisitionResult:
    if version != ADMITTED_VERSION:
        return CoreAcquisitionResult(version=version, filename="", path=Path(""), origin="", sha256="", verified=False,
                                   error="version not admitted", error_kind=AcquisitionErrorKind.VERSION_NOT_ADMITTED)

    origin = "github-release:NicolasBruna24/CastleArq@v0.6.0"
    tmp_root = Path(tempfile.mkdtemp(prefix="castlearq-core-"))
    target = tmp_root / EXPECTED_WHEEL

    try:
        req = urllib.request.Request(OFFICIAL_URL, headers={"User-Agent": "CastleArq-Desktop"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    return CoreAcquisitionResult(version=version, filename=EXPECTED_WHEEL, path=Path(""), origin=origin, sha256="", verified=False,
                                               error=f"http {status}", error_kind=AcquisitionErrorKind.RELEASE_NOT_FOUND)
                data = resp.read()
        except urllib.error.HTTPError as e:
            kind = AcquisitionErrorKind.ASSET_MISSING if e.code == 404 else AcquisitionErrorKind.NETWORK
            return CoreAcquisitionResult(version=version, filename=EXPECTED_WHEEL, path=Path(""), origin=origin, sha256="", verified=False,
                                       error=f"http {e.code}", error_kind=kind)
        except urllib.error.URLError as e:
            return CoreAcquisitionResult(version=version, filename=EXPECTED_WHEEL, path=Path(""), origin=origin, sha256="", verified=False,
                                       error=str(e), error_kind=AcquisitionErrorKind.NETWORK)

        if not data:
            for p in tmp_root.iterdir():
                p.unlink(missing_ok=True)
            tmp_root.rmdir()
            return CoreAcquisitionResult(version=version, filename=EXPECTED_WHEEL, path=Path(""), origin=origin, sha256="", verified=False,
                                       error="incomplete download", error_kind=AcquisitionErrorKind.INCOMPLETE_DOWNLOAD)

        target.write_bytes(data)
        actual = _sha256_file(target)

        if actual != EXPECTED_SHA256:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                tmp_root.rmdir()
            except Exception:
                pass
            return CoreAcquisitionResult(version=version, filename=EXPECTED_WHEEL, path=Path(""), origin=origin, sha256=actual, verified=False,
                                       error="hash mismatch", error_kind=AcquisitionErrorKind.HASH_MISMATCH)

        final_path = target
        if destination_dir is not None:
            destination_dir.mkdir(parents=True, exist_ok=True)
            final_path = destination_dir / EXPECTED_WHEEL
            final_path.write_bytes(target.read_bytes())
            target.unlink(missing_ok=True)
            tmp_root.rmdir()

        return CoreAcquisitionResult(version=version, filename=EXPECTED_WHEEL, path=final_path, origin=origin, sha256=actual, verified=True)
    except Exception as e:  # noqa: BLE001
        try:
            for p in tmp_root.iterdir():
                p.unlink(missing_ok=True)
            tmp_root.rmdir()
        except Exception:
            pass
        return CoreAcquisitionResult(version=version, filename=EXPECTED_WHEEL, path=Path(""), origin=origin, sha256="", verified=False,
                                   error=str(e), error_kind=AcquisitionErrorKind.NETWORK)
