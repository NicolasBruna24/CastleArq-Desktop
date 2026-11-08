# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from castlearq_desktop.core.managed_environment import discover_managed_environment
from castlearq_desktop.core.managed_environment_installer import (
    install_core_0_6_0, InstallStatus, TARGET_VERSION,
)


def test_regression_discover_root_override(tmp_path):
    root = tmp_path / "custom-root"
    root.mkdir()
    py = root / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    res = discover_managed_environment(root_override=root)
    assert res.root_path == root


def test_concurrent_installations(tmp_path):
    lock = threading.Lock()
    results = []

    def worker():
        r = install_core_0_6_0(root_override=tmp_path / "env")
        results.append(r)

    # force first to hold lock by patching acquisition to sleep? simpler: call twice quickly
    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    # at least one failed or both; no crash
    assert len(results) == 2
def _make_candidate_with_broken_shebangs(candidate: Path, candidate_str: str) -> None:
    """Simula el estado de un candidato tras ``python -m venv`` + ``pip install``:
    ``bin/python3`` presente y los scripts de consola cuyos shebang apuntan al
    candidato temporal (defectuosos tras el swap).
    """
    (candidate / "bin").mkdir(parents=True)
    (candidate / "bin" / "python3").symlink_to("/usr/bin/python3")
    (candidate / "bin" / "pip").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys\nsys.exit(0)\n"
    )
    (candidate / "bin" / "pip3").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys\nsys.exit(0)\n"
    )
    (candidate / "bin" / "castlearq").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys\nsys.exit(0)\n"
    )


def _write_fake_wheel(path: Path) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "castlearq-0.6.0.dist-info/METADATA",
            "[metadata]\nname=castlearq\nversion=0.6.0\n",
        )
        z.writestr("castlearq-0.6.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        z.writestr("castlearq-0.6.0.dist-info/top_level.txt", "castlearq\n")
        z.writestr("castlearq/__init__.py", "def main():\n    pass\n")


def test_repair_script_shebangs_repairs_pip_and_castlearq(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)

    candidate = tmp_path / "candidate-broken"
    candidate.mkdir()
    candidate_str = str(candidate)

    # Escribir los scripts de root/bin con shebang apuntando al candidato
    # (escenario post-place, cundo el candidato se renombra a root).
    (root / "bin" / "pip").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys\n" "sys.exit(0)\n"
    )
    (root / "bin" / "pip3").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys\n" "sys.exit(0)\n"
    )
    (root / "bin" / "castlearq").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys\n" "sys.exit(0)\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _repair_script_shebangs,
    )

    _repair_script_shebangs(root, candidate)

    expected_shebang = b"#!" + str(root / "bin" / "python3").encode() + b"\n"
    for name in ("pip", "pip3", "castlearq"):
        content = (root / "bin" / name).read_bytes()
        assert content.startswith(expected_shebang), name

def test_repair_script_shebangs_ignores_symlinks(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)
    (root / "bin" / "pip").symlink_to("/usr/local/bin/pip-real")

    candidate = tmp_path / "candidate-symlink"
    candidate.mkdir()
    candidate_str = str(candidate)

    # pip en root/bin es un symlink (se debe ignorar); el otro script con
    # shebang del candidato queda intacto.
    (root / "bin" / "castlearq").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys; sys.exit(0)\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _repair_script_shebangs,
    )

    _repair_script_shebangs(root, candidate)
    assert (root / "bin" / "pip").is_symlink()
    assert str((root / "bin" / "pip").readlink()) == "/usr/local/bin/pip-real"
    # El script con shebang del candidato se reparó
    assert (root / "bin" / "castlearq").read_bytes().startswith(
        b"#!" + str(root / "bin" / "python3").encode() + b"\n"
    )

def test_repair_script_shebangs_leaves_external_interpreter_unchanged(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)
    (root / "bin" / "pip").write_bytes(b"#!/usr/bin/python3\nimport sys; sys.exit(0)\n")
    (root / "bin" / "castlearq").write_bytes(b"#!/usr/local/bin/python\nimport sys; sys.exit(0)\n")

    candidate = tmp_path / "candidate-external"
    candidate.mkdir()
    candidate_str = str(candidate)
    # El script pip con shebang del candidato se reparará; los de intérprete
    # externo quedarán intactos.
    (root / "bin" / "pip").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys; sys.exit(0)\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _repair_script_shebangs,
    )

    _repair_script_shebangs(root, candidate)
    assert (root / "bin" / "pip").read_bytes().startswith(b"#!" + str(root / "bin" / "python3").encode())
    assert (root / "bin" / "castlearq").read_bytes().startswith(b"#!/usr/local/bin/python")

def test_repair_script_shebangs_preserves_permissions(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)

    candidate = tmp_path / "candidate-perms"
    candidate.mkdir()
    candidate_str = str(candidate)

    (root / "bin" / "pip").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys; sys.exit(0)\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _repair_script_shebangs,
    )

    pip = root / "bin" / "pip"
    pip.chmod(0o700)
    _repair_script_shebangs(root, candidate)
    assert (pip.stat().st_mode & 0o111) == 0o100

def test_repair_script_shebangs_idempotent(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)

    candidate = tmp_path / "candidate-idem"
    candidate.mkdir()
    candidate_str = str(candidate)

    (root / "bin" / "pip").write_text(
        "#!" + candidate_str + "/bin/python3\n" "import sys; sys.exit(0)\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _repair_script_shebangs,
    )

    _repair_script_shebangs(root, candidate)
    first = (root / "bin" / "pip").read_bytes()
    _repair_script_shebangs(root, candidate)
    second = (root / "bin" / "pip").read_bytes()
    assert first == second

def test_verify_no_candidate_references_passes_when_clean(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)
    (root / "bin" / "pip").write_bytes(
        b"#!" + str(root / "bin" / "python3").encode() + b"\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _verify_no_candidate_references,
    )

    _verify_no_candidate_references(root, tmp_path / "candidate-clean")


def test_verify_no_candidate_references_raises_when_offending(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)
    (root / "bin" / "pip").write_bytes(
        b"#!" + str(tmp_path / "candidate-broken").encode() + b"/bin/python3\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _verify_no_candidate_references,
    )

    with pytest.raises(RuntimeError, match="conservan referencia al candidato"):
        _verify_no_candidate_references(root, tmp_path / "candidate-broken")


def test_validate_installed_environment_passes_with_mocks(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)
    (root / "bin" / "pip").write_bytes(
        b"#!" + str(root / "bin" / "python3").encode() + b"\nimport sys; sys.exit(0)\n"
    )
    (root / "bin" / "castlearq").write_bytes(
        b"#!" + str(root / "bin" / "python3").encode() + b"\nimport sys; sys.exit(0)\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _validate_installed_environment,
    )

    with patch(
        "castlearq_desktop.core.managed_environment_installer.subprocess_runner",
        return_value=MagicMock(returncode=0),
    ), patch(
        "castlearq_desktop.core.managed_environment_installer.query_installed_core_version",
        return_value="0.6.0",
    ):
        _validate_installed_environment(root, tmp_path / "candidate-valid")


def test_install_success_repairs_shebangs_and_validates(tmp_path):
    root = tmp_path / "env"
    wheel = tmp_path / "castlearq-0.6.0-py3-none-any.whl"
    _write_fake_wheel(wheel)

    candidate_str = "placeholder"

    from castlearq_desktop.core.managed_environment_installer import (
        InstallResult,
        InstallStatus,
        install_core_0_6_0,
    )
    from castlearq_desktop.core.core_acquisition_service import (
        CoreAcquisitionResult,
    )

    acq = CoreAcquisitionResult(
        version="0.6.0",
        filename="castlearq-0.6.0-py3-none-any.whl",
        path=wheel,
        origin="test",
        sha256="0" * 64,
        verified=True,
    )

    mock_subprocess = MagicMock(return_value=MagicMock(returncode=0, stdout="3 14\n"))
    with patch(
        "castlearq_desktop.core.managed_environment_installer.subprocess_runner",
        mock_subprocess,
    ), patch(
        "castlearq_desktop.core.managed_environment_installer.validate_python_interpreter",
        return_value=MagicMock(is_valid=True),
    ), patch(
        "castlearq_desktop.core.managed_environment_installer.query_installed_core_version",
        return_value="0.6.0",
    ):
        result = install_core_0_6_0(
            root_override=root,
            acquisition_func=lambda version="0.6.0": acq,
            venv_creator=lambda cand: _make_candidate_with_broken_shebangs(Path(cand), str(Path(cand))),
            subprocess_runner=mock_subprocess,
        )

    assert result.status == InstallStatus.SUCCESS
    assert result.root_path == root

    expected_shebang = b"#!" + str(root / "bin" / "python3").encode() + b"\n"
    for name in ("pip", "pip3", "castlearq"):
        content = (root / "bin" / name).read_bytes()
        assert content.startswith(expected_shebang), name

    leftovers = list((root.parent).glob("managed-python-candidate-*"))
    assert leftovers == [], leftovers


def test_install_post_swap_validation_fails_and_preserves_previous(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    # Instalación anterior válida con marcador
    (root / "MARKER_PREVIOUS").write_text("previous-env\n")
    (root / "MARKER_PREVIOUS").chmod(0o644)

    wheel = tmp_path / "castlearq-0.6.0-py3-none-any.whl"
    _write_fake_wheel(wheel)

    candidate_str = str(tmp_path / "managed-python-candidate-break")
    candidate = Path(candidate_str)
    candidate.mkdir()
    # Candidato sin bin/python3 para que la reparación falle
    (candidate / "bin").mkdir(parents=True)
    (candidate / "bin" / "pip").write_text(
        "#!" + candidate_str + "/bin/python3\nimport sys; sys.exit(0)\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        InstallResult,
        InstallStatus,
        install_core_0_6_0,
    )
    from castlearq_desktop.core.core_acquisition_service import (
        CoreAcquisitionResult,
    )

    acq = CoreAcquisitionResult(
        version="0.6.0",
        filename="castlearq-0.6.0-py3-none-any.whl",
        path=wheel,
        origin="test",
        sha256="0" * 64,
        verified=True,
    )

    mock_subprocess = MagicMock(return_value=MagicMock(returncode=0, stdout="3 14\n"))
    with patch(
        "castlearq_desktop.core.managed_environment_installer.subprocess_runner",
        mock_subprocess,
    ), patch(
        "castlearq_desktop.core.managed_environment_installer.validate_python_interpreter",
        return_value=MagicMock(is_valid=True),
    ), patch(
        "castlearq_desktop.core.managed_environment_installer.query_installed_core_version",
        return_value="0.6.0",
    ), patch(
        "castlearq_desktop.core.managed_environment_installer._repair_script_shebangs",
        side_effect=RuntimeError("reparacion intencional" ),
    ):
        result = install_core_0_6_0(
            root_override=root,
            acquisition_func=lambda version="0.6.0": acq,
            venv_creator=lambda cand: _make_candidate_with_broken_shebangs(Path(cand), str(Path(cand))),
            subprocess_runner=mock_subprocess,
            cancel_token=None,
        )

    assert result.status == InstallStatus.FAILED
    assert result.error is not None
    assert result.root_path == root

    # La instalación anterior no se ha borrado ni modificado: tras la
    # restauración el entorno anterior ha pasado a ocupar `root`.
    assert (root / "MARKER_PREVIOUS").read_text() == "previous-env\n"
    # El .previous fue movido a root (no debe existir en su ruta original).
    assert not (root.parent / (root.name + ".previous")).exists()
    # El candidato nuevo queda como .failed (no eliminado)
    failed = (root.parent / (root.name + ".failed"))
    # La restauración se completó correctamente: el entorno anterior está en root
    assert (root / "MARKER_PREVIOUS").read_text() == "previous-env\n"


def test_validate_installed_environment_fails_on_broken_pip(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "python3").write_text("#!/bin/sh\nexec /usr/bin/python3\n")
    (root / "bin" / "python3").chmod(0o755)
    (root / "bin" / "pip").write_bytes(
        b"#!" + str(tmp_path / "candidate-broken").encode() + b"/bin/python3\n"
    )

    from castlearq_desktop.core.managed_environment_installer import (
        _validate_installed_environment,
    )

    with patch(
        "castlearq_desktop.core.managed_environment_installer.subprocess_runner",
        return_value=MagicMock(returncode=0),
    ), patch(
        "castlearq_desktop.core.managed_environment_installer.query_installed_core_version",
        return_value="0.6.0",
    ):
        with pytest.raises(RuntimeError, match="conservan referencia al candidato"):
            _validate_installed_environment(root, tmp_path / "candidate-broken")


def test_install_cancelation_and_recovery_preserves_previous(tmp_path):
    root = tmp_path / "env"
    (root / "bin").mkdir(parents=True)
    (root / "MARKER_PREVIOUS").write_text("previous-env\n")
    (root / "MARKER_PREVIOUS").chmod(0o644)

    wheel = tmp_path / "castlearq-0.6.0-py3-none-any.whl"
    _write_fake_wheel(wheel)

    candidate_str = str(tmp_path / "managed-python-candidate-cancel")
    candidate = Path(candidate_str)

    from castlearq_desktop.core.managed_environment_installer import (
        InstallResult,
        InstallStatus,
        _CancelToken,
        install_core_0_6_0,
    )
    from castlearq_desktop.core.core_acquisition_service import (
        CoreAcquisitionResult,
    )
    acq = CoreAcquisitionResult(
        version="0.6.0",
        filename="castlearq-0.6.0-py3-none-any.whl",
        path=wheel,
        origin="test",
        sha256="0" * 64,
        verified=True,
    )

    mock_subprocess = MagicMock(return_value=MagicMock(returncode=0))
    cancel_token = _CancelToken()

    def fake_subprocess(argv, **kw):
        if len(argv) >= 3 and argv[1] == "-m" and argv[2] == "venv":
            cancel_token.request_cancellation()
        return MagicMock(returncode=0)

    with patch(
        "castlearq_desktop.core.managed_environment_installer.subprocess_runner",
        side_effect=fake_subprocess,
    ), patch(
        "castlearq_desktop.core.managed_environment_installer.query_installed_core_version",
        return_value="0.6.0",
    ):
        result = install_core_0_6_0(
            root_override=root,
            acquisition_func=lambda version="0.6.0": acq,
            venv_creator=lambda cand: (_make_candidate_with_broken_shebangs(Path(cand), candidate_str), cancel_token.request_cancellation()),
            subprocess_runner=mock_subprocess,
            cancel_token=cancel_token,
        )

    assert result.status == InstallStatus.CANCELLED
    assert result.error is not None
    # La instalación anterior no se ha borrado ni modificado
    assert (root / "MARKER_PREVIOUS").read_text() == "previous-env\n"
    # No hay candidato residual en la ubicación de raíces
    assert not (root.parent / (root.name + ".previous")).exists()
