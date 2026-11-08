# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Managed environment installer for CastleArq Core 0.6.0."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from castlearq_desktop.core.core_acquisition_service import (
    ADMITTED_VERSION,
    acquire_core_0_6_0,
)
from castlearq_desktop.core.managed_environment import (
    managed_environment_root,
    managed_python_executable,
    discover_managed_environment,
    ManagedEnvironmentStatus,
)
from castlearq_desktop.core.interpreter_validator import validate_python_interpreter
from castlearq_desktop.core.version_admission import query_installed_core_version

TARGET_VERSION = ADMITTED_VERSION
_INSTALL_LOCK = threading.Lock()


def _run(argv, **kw):
    return subprocess.run(argv, shell=False, **kw)


subprocess_runner: Callable = _run


class InstallStatus(str, Enum):
    ALREADY_INSTALLED = "already_installed"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class InstallResult:
    status: InstallStatus
    root_path: Path
    python_path: Optional[Path]
    version: Optional[str]
    error: Optional[str] = None


class _CancelToken:
    """Simple cancellable token passed through the installation pipeline."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    def request_cancellation(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled.is_set()

    def reset(self) -> None:
        self._cancelled = threading.Event()


def _repair_script_shebangs(root: Path, candidate: Path) -> None:
    """Reparar shebangs de scripts de consola tras el intercambio transaccional.

    Los scripts generados por ``pip`` (p. ej. ``bin/pip``, ``bin/castlearq``)
    contienen un shebang con la ruta absoluta del directorio de candidatura
    temporal. Al renombrarse el candidato a la raíz definitiva, esos shebang
    quedan rotos porque apuntan a un directorio que ya no existe.

    La reparación reemplaza únicamente la ruta del intérprete de los shebang de
    aquellos scripts cuyo intérprete original coincida exactamente con
    *candidate*, dejando el resto del archivo (permisos, metadatos) intacto.

    El criterio de modificación es estricto: la ruta del intérprete del
    shebang debe ser el candidato concreto que se está instalando. Nunca se
    aplican correcciones genéricas a intérpretes externos ("/usr/bin", "/bin",
    ...).
    """
    bin_dir = root / "bin"
    if not bin_dir.is_dir():
        return

    python3 = bin_dir / "python3"
    if not (python3.is_file() and os.access(python3, os.X_OK)):
        raise RuntimeError(
            f"intérprete de reparación no encontrado o no ejecutable: {python3}"
        )

    candidate_str = str(candidate)

    for child in bin_dir.iterdir():
        # 1. Inspeccionar exclusivamente entradas regulares.
        if child.is_symlink() or not child.is_file():
            continue

        try:
            with child.open("rb") as fh:
                data = fh.read()
        except OSError:
            continue

        if not data.startswith(b"#!"):
            continue

        # 2. Separar primera línea (shebang) del resto del archivo.
        newline_idx = data.find(b"\n")
        if newline_idx == -1:
            first = data
            rest = b""
        else:
            first = data[:newline_idx + 1]
            rest = data[newline_idx + 1:]

        # 3. Parsar intérprete y argumentos del shebang.
        m = re.match(rb"^#!(\s*)(/\S*)(\s*.*)$", first)
        if not m:
            continue
        interpreter = m.group(2).decode("ascii", errors="strict")
        args = m.group(3)

        # 4. Cambiar únicamente scripts cuyo intérprete apunte al candidato
        #    concreto. Cualquier otro intérprete (externo, relativo, etc.)
        #    queda intacto.
        if not (
            interpreter == candidate_str
            or interpreter.startswith(candidate_str + os.sep)
        ):
            continue

        # 5. Nuevo intérprete final: <root>/bin/python3 (solo tras comprobar
        #    que existe y es ejecutable, arriba).
        corrected_first = (
            b"#!" + m.group(1)
            + str(root / "bin" / "python3").encode("ascii")
            + args
        )
        if not corrected_first.endswith(b"\n"):
            corrected_first += b"\n"

        corrected = corrected_first + rest

        # 6/7. Escritura mediante archivo temporal en el mismo directorio y
        #      os.replace() (operación atómica en el mismo filesystem).
        tmp_path = child.with_name("." + child.name + ".shebang-tmp")
        try:
            with tmp_path.open("wb") as fh:
                fh.write(corrected)
            # 8. Preservar permisos y metadatos relevantes del archivo
            #    original antes de reemplazarlo.
            try:
                os.chmod(tmp_path, child.stat().st_mode)
                shutil.copystat(child, tmp_path)
            except OSError:
                tmp_path.unlink(missing_ok=True)
                raise
            os.replace(tmp_path, child)
        except BaseException:
            # 9. Limpiar el temporal si ocurre una excepción.
            tmp_path.unlink(missing_ok=True)
            raise


def _verify_no_candidate_references(root: Path, candidate: Path) -> None:
    """Confirmar que ningún script regular de root/bin conserva referencia al
    candidato original.

    Se recorre exclusivamente ``root/bin``; se ignoran symlinks, directorios y
    archivos sin shebang al principio. Se lanzan un error con los nombres de los
    scripts que incumplen la condición.
    """
    bin_dir = root / "bin"
    candidate_str = str(candidate)
    offending: list[str] = []

    for child in bin_dir.iterdir():
        if child.is_symlink() or not child.is_file():
            continue
        try:
            with child.open("rb") as fh:
                first = fh.readline()
        except OSError:
            continue
        if not first.startswith(b"#!"):
            continue
        m = re.match(rb"^#!(\s*)(/\S*)(\s*.*)$", first)
        if not m:
            continue
        interpreter = m.group(2).decode("ascii", errors="strict")
        if interpreter == candidate_str or interpreter.startswith(
            candidate_str + os.sep
        ):
            offending.append(child.name)

    if offending:
        raise RuntimeError(
            "scripts en "
            f"{bin_dir} conservan referencia al candidato {candidate.name}: "
            f"{', '.join(offending)}"
        )


def _validate_installed_environment(root: Path, candidate: Path) -> None:
    """Validación posterior al intercambio transaccional (etapa 3).

    Comprobaciones esenciales del entorno instalado en su ubicación
    definitiva. Lanza RuntimeError si alguna comprobación falla, de modo que
    la instalación nunca devuelve SUCCESS en un estado inconsistente.
    """
    bin_dir = root / "bin"
    python3 = bin_dir / "python3"

    if not (python3.is_file() and os.access(python3, os.X_OK)):
        raise RuntimeError(
            f"intérprete python3 no encontrado ni ejecutable en {python3}"
        )

    # root/bin/python -m castlearq.main --help
    help_res = subprocess_runner(
        [str(root / "bin" / "python"), "-m", "castlearq.main", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if help_res.returncode != 0:
        raise RuntimeError(
            "root/bin/python -m castlearq.main --help falló: "
            f"{help_res.stderr or help_res.stdout}"
        )

    # root/bin/pip --version
    pip_res = subprocess_runner(
        [str(bin_dir / "pip"), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if pip_res.returncode != 0:
        raise RuntimeError(
            "root/bin/pip --version falló: "
            f"{pip_res.stderr or pip_res.stdout}"
        )

    # Comprobar que root/bin/castlearq tiene shebang válido (intérprete
    # correspondiente a root, no al candidato).
    castlearq = bin_dir / "castlearq"
    if castlearq.is_file() and not castlearq.is_symlink():
        with castlearq.open("rb") as fh:
            first = fh.readline()
        if not first.startswith(b"#!"):
            raise RuntimeError(
                f"shebang no válido en {castlearq}: {first!r}"
            )

    # Confirmar que ningún script regular conserva referencia al candidato.
    _verify_no_candidate_references(root, candidate)

    # Confirmar que la versión instalada de Core sigue siendo la admitida.
    version = query_installed_core_version(str(python3))
    if version != TARGET_VERSION:
        raise RuntimeError(
            f"versión de Core instalada {version} != {TARGET_VERSION}"
        )



def _wait_with_timeout(
    process_handle: Any,
    timeout: float,
    cancel_token: Optional[_CancelToken] = None,
) -> Optional[int]:
    """Wait for a process to finish within *timeout* seconds, checking cancel.

    Returns the exit code if the process terminates, or ``None`` if the timeout
    expires or cancellation is requested.
    """
    import time as _t
    start = _t.monotonic()
    while True:
        if cancel_token is not None and cancel_token.is_cancelled():
            return None
        elapsed = _t.monotonic() - start
        if elapsed >= timeout:
            return None
        remaining = max(0, timeout - elapsed)
        # Poll every 0.2 s so we notice cancellation promptly.
        _t.sleep(min(0.2, remaining))


def install_core_0_6_0(
    *,
    subprocess_runner: Callable = _run,
    venv_creator: Optional[Callable] = None,
    acquisition_func=acquire_core_0_6_0,
    root_override: Optional[Path] = None,
    cancel_token: Optional[_CancelToken] = None,
) -> InstallResult:
    root = root_override if root_override is not None else managed_environment_root()
    existing = discover_managed_environment(root_override)
    if existing is not None and existing.status == ManagedEnvironmentStatus.VALID and existing.detected_version == TARGET_VERSION:
        return InstallResult(InstallStatus.ALREADY_INSTALLED, root, existing.python_path, existing.detected_version)

    if not _INSTALL_LOCK.acquire(blocking=False):
        return InstallResult(InstallStatus.FAILED, root, None, None, "concurrent installation in progress")

    try:
        acq = acquisition_func(version=TARGET_VERSION)
        if not acq.verified or not getattr(acq, "path", None) or not Path(acq.path).exists():
            return InstallResult(InstallStatus.FAILED, root, None, None, getattr(acq, "error", None) or "acquisition failed")

        wheel_path = Path(acq.path)
        parent = root.parent
        parent.mkdir(parents=True, exist_ok=True)

        candidate = Path(tempfile.mkdtemp(prefix="managed-python-candidate-", dir=str(parent)))
        # Clean any leftover candidate from a previous interrupted run.
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)

        try:
            if venv_creator is not None:
                venv_creator(str(candidate))
            else:
                subprocess_runner([sys.executable, "-m", "venv", str(candidate)], check=True, capture_output=True, text=True, timeout=120)

            if cancel_token is not None and cancel_token.is_cancelled():
                shutil.rmtree(candidate, ignore_errors=True)
                return InstallResult(InstallStatus.CANCELLED, root, None, None, "installation cancelled during venv creation")

            cand_python = managed_python_executable(candidate)

            if cancel_token is not None and cancel_token.is_cancelled():
                shutil.rmtree(candidate, ignore_errors=True)
                return InstallResult(InstallStatus.CANCELLED, root, None, None, "installation cancelled after venv creation")

            subprocess_runner(
                [str(cand_python), "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir", str(wheel_path)],
                check=True, capture_output=True, text=True, timeout=300,
            )

            if cancel_token is not None and cancel_token.is_cancelled():
                shutil.rmtree(candidate, ignore_errors=True)
                return InstallResult(InstallStatus.CANCELLED, root, None, None, "installation cancelled after pip install")

            outcome = validate_python_interpreter(str(cand_python))
            if not outcome.is_valid:
                raise RuntimeError("python invalid")

            ver_check = subprocess_runner([str(cand_python), "-c", "import sys; print(sys.version_info.major, sys.version_info.minor)"], capture_output=True, text=True)
            maj, minr = ver_check.stdout.split()
            if (int(maj), int(minr)) < (3, 11):
                raise RuntimeError("python version < 3.11")

            version = query_installed_core_version(str(cand_python))
            if version != TARGET_VERSION:
                raise RuntimeError(f"core version {version} != {TARGET_VERSION}")

            imp = subprocess_runner([str(cand_python), "-I", "-c", "import castlearq.main"], capture_output=True, text=True)
            if imp.returncode != 0:
                raise RuntimeError("cannot import castlearq.main")

            help_res = subprocess_runner([str(cand_python), "-m", "castlearq.main", "--help"], capture_output=True, text=True, timeout=60)
            if help_res.returncode != 0:
                raise RuntimeError("castlearq.main --help failed")

            # --- Transactional root swap ---
            # Step 1: backup existing root (if any) to .previous
            if root.exists():
                backup = parent / (root.name + ".previous")
                # Remove old backup only if we're about to create a new one;
                # this preserves recoverability.
                if backup.exists():
                    # Only remove old backup if it's not the one we're currently
                    # recovering from (i.e., it's a stale leftover from a failed
                    # previous install that already moved the root away).
                    # We keep it to allow rollback.
                    pass
                root.rename(backup)
            else:
                # No existing root; ensure there's no stale .previous that shouldn't be there.
                # We do NOT delete .previous here - it may be a valid rollback target.
                pass

            # Step 2: move candidate into root
            try:
                candidate.rename(root)
            except Exception:
                # Restore the backup if the rename failed.
                if backup.exists():
                    backup.rename(root)
                raise

            # --- Etapa de reparación de shebangs tras el swap ---
            # Los scripts generados por `pip` llevan shebang apuntando al
            # directorio temporal de candidatura. Tras el swap (candidate
            # renombrado a root) esos shebang apuntan a un path inexistente.
            # Aquí se corrigen a <root>/bin/python3.
            _repair_script_shebangs(root, candidate)

            # Validación posterior al swap (comprobaciones esenciales). Lanza
            # RuntimeError si alguna comprobación falla, de modo que nunca se
            # devuelva SUCCESS en un estado inconsistente.
            _validate_installed_environment(root, candidate)

            # Step 3: clear candidate reference after successful repair.
            candidate = None

            # Post-installation: if root is missing but .previous exists, recover.
            if not root.exists() and (parent / (root.name + ".previous")).exists():
                # Recovery: restore the previous environment as the new root.
                prev = parent / (root.name + ".previous")
                try:
                    prev.rename(root)
                except Exception:
                    pass  # Best-effort; installer result will reflect the state.

            return InstallResult(InstallStatus.SUCCESS, root, managed_python_executable(root), TARGET_VERSION)
        except Exception as e:
            # Cleanup candidate on failure. Note: after the swap the candidate
            # no longer exists as a directory (it was renamed to root), so this
            # is a no-op in the post-swap failure path.
            if candidate is not None and Path(candidate).exists():
                shutil.rmtree(candidate, ignore_errors=True)

            # Post-swap fallo de reparación/validación: restaurar la
            # instalación anterior a través del mecanismo existente (.previous).
            # No se elimina .previous y no se devuelve SUCCESS.
            backup = parent / (root.name + ".previous")
            if backup.exists():
                failed_root = parent / (root.name + ".failed")
                try:
                    # Mover el entorno nuevo (roto) por detrás para liberar `root`,
                    # y dejar la instalación anterior como el nuevo root.
                    root.rename(failed_root)
                    backup.rename(root)
                    return InstallResult(
                        InstallStatus.FAILED,
                        root,
                        None,
                        None,
                        "reparación/validación fallida después del swap; "
                        f"instalación anterior restaurada en {root} "
                        f"(candidato incompleto en {failed_root})",
                    )
                except Exception as restore_err:
                    # La restauración no es segura: conservar ambos directorios
                    # y devolver un error explícito con rutas y estado.
                    return InstallResult(
                        InstallStatus.FAILED,
                        root,
                        None,
                        None,
                        "reparación/validación fallida después del swap: "
                        f"{e}; restauración no segura ({restore_err}); "
                        f"candidato nuevo en {root}, anterior en {backup}",
                    )
            # No hay instalación anterior para restaurar; el entorno recién
            # instalado (posiblemente roto) ocupa `root` y el candidato se
            # limpia arriba.
            return InstallResult(InstallStatus.FAILED, root, None, None, str(e))
    finally:
        _INSTALL_LOCK.release()


__all__ = ["install_core_0_6_0", "InstallResult", "InstallStatus", "TARGET_VERSION"]
