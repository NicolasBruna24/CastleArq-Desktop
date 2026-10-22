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

"""Application bootstrap, composition root, and Qt application lifecycle.

This module owns creation of the ``QApplication`` and the top-level ``exec``
loop, and acts as the composition root: it constructs the production
dependencies (settings store, monotonic clock, ``CoreStartupCoordinator``) and
the application-level ``StartupController``, wires the controller into the main
window, and owns the controller for the whole application lifetime
independently of the window (ADR-0002 D7).

It performs no Core orchestration itself: process launch, readiness, and
shutdown are delegated to the controller. The only policy it owns is translating
a terminal ``SHUTDOWN_FAILED`` outcome into a non-zero process exit code.
"""

from __future__ import annotations
import logging
import logging.handlers
import os

import sys
from typing import List, Optional

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from castlearq_desktop import APP_NAME, ORGANIZATION_DOMAIN, ORGANIZATION_NAME
from castlearq_desktop.core.contracts import VersionAdmission
from castlearq_desktop.core.interpreter_settings import create_interpreter_settings
from castlearq_desktop.core.lifecycle_manager import LifecycleState
from castlearq_desktop.core.startup_controller import MonotonicClock, StartupController
from castlearq_desktop.core.startup_coordinator import CoreStartupCoordinator
from castlearq_desktop.ui.main_window import MainWindow


def create_application(argv: Optional[List[str]] = None) -> QApplication:
    """Create (or reuse) the Qt application and apply the approved identity.

    Reuses an existing ``QApplication`` instance when one is already present so
    that the application identity is configured exactly once. No Core process is
    created, requested, or contacted here.
    """
    app = QApplication.instance() or QApplication(argv or [])
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setOrganizationDomain(ORGANIZATION_DOMAIN)
    app.setApplicationName(APP_NAME)
    return app


def shutdown_exit_code(state: object) -> int:
    """Map a terminal shutdown outcome to a process exit code (ADR-0002 D7).

    ``SHUTDOWN_FAILED`` (the application-level deadline expired without a
    confirmed child exit) is the only outcome that yields a non-zero code; a
    confirmed exit (``STOPPED``) yields ``0``.
    """
    return 1 if state is LifecycleState.SHUTDOWN_FAILED else 0



#: Loggers whose DEBUG messages are enabled only when opt-in readiness
#: debugging is requested through the environment variable.
_READINESS_LOGGERS = (
    "castlearq_desktop.core.readiness_qt_transport",
    "castlearq_desktop.ui.main_window",
)

#: Maximum size (bytes) for each readiness debug log file before rotation.
_READINESS_LOG_MAX_BYTES = 2 * 1024 * 1024

#: Number of backup files to keep when rotating.
_READINESS_LOG_BACKUP_COUNT = 1

#: Log message format: ``timestamp | logger | level | message``.
_READINESS_LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"


def _readiness_log_path() -> str:
    """Return the temporary log file path including the current process PID."""
    return f"/tmp/castlearq_desktop_readiness_debug.{os.getpid()}.log"


def _configure_readiness_debug_logging() -> None:
    """Configure file logging for the readiness loggers (opt-in).",
    """
    Installs a rotating ``FileHandler`` on each readiness logger so that
    DEBUG messages are captured without touching the root logger nor enabling
    DEBUG globally. If the file cannot be created or configured, a clear
    message is written to ``stderr`` and readiness is left untouched.
    """
    log_file = _readiness_log_path()
    logger_names = _READINESS_LOGGERS

    # Locate an existing reusable handler before opening a new file.
    existing_handler = None
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        for h in logger.handlers:
            if (
                isinstance(h, logging.handlers.RotatingFileHandler)
                and getattr(h, "baseFilename", None) == log_file
            ):
                existing_handler = h
                break
        if existing_handler is not None:
            break

    created_handler = None
    handler = existing_handler
    try:
        if existing_handler is None:
            # No reusable handler found; create a single shared one.
            created_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=_READINESS_LOG_MAX_BYTES,
                backupCount=_READINESS_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            created_handler.setLevel(logging.DEBUG)
            created_handler.setFormatter(logging.Formatter(_READINESS_LOG_FORMAT))
            handler = created_handler
        # else: reuse the existing handler in all loggers.
    except Exception as exc:
        # Handler configuration failed (OSError or anything else): report it on
        stderr but keep going, so that the DEBUG level of every readiness
        logger is still restored. Logging diagnostics must never abort the
        application startup.
        if created_handler is not None:
            try:
                created_handler.close()
            except Exception:
                pass
            created_handler = None
        handler = None
        sys.stderr.write(
            f"castlearq-desktop: could not configure readiness debug logging: {exc}\n"
        )

    # Install the handler (reused or newly created) into each readiness logger.
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        if handler is not None:
            try:
                if not any(
                    isinstance(h, logging.handlers.RotatingFileHandler)
                    and getattr(h, "baseFilename", None) == log_file
                    for h in logger.handlers
                ):
                    # Reuse or install a single shared file handle.
                    logger.addHandler(handler)
            except Exception as exc:  # pragma: no cover - defensive
                sys.stderr.write(
                    f"castlearq-desktop: readiness debug logging for "
                    f"{logger_name} could not be configured: {exc}\n"
                )

        # Ensure the logger's DEBUG level is restored, regardless of whether a
        # handler was added successfully. Failures are reported but never abort
        # startup.
        try:
            logger.setLevel(logging.DEBUG)
        except Exception as exc:  # pragma: no cover - defensive
            sys.stderr.write(
                f"castlearq-desktop: readiness debug logging for "
                f"{logger_name} could not set level: {exc}\n"
            )

    # Close only the handler this call created, and only if it is not
    # installed in any logger. Never close a handler that already existed.
    if created_handler is not None:
        try:
            still_installed = any(
                h is created_handler
                for logger_name in logger_names
                for h in logging.getLogger(logger_name).handlers
            )
            if not still_installed:
                created_handler.close()
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    """Run the application shell and return a process exit code.

    Acts as the composition root: builds the production dependencies and the
    ``StartupController``, injects the controller into the window, and keeps the
    controller alive for the whole run (ADR-0002 D7). Window close is routed
    through the controller, which coordinates a non-blocking shutdown; the event
    loop is allowed to end only after a terminal shutdown outcome, at which
    point a ``SHUTDOWN_FAILED`` outcome is translated into a non-zero exit code.
    """
    if os.environ.get("CASTLEARQ_READINESS_DEBUG") == "1":
        _configure_readiness_debug_logging()
    app = create_application(argv)

    settings = create_interpreter_settings()
    clock = MonotonicClock()
    # DEC-A (conservative admission): the static input stays NOT_EVALUATED
    # (fail-closed); per-pass dynamics engage through the coordinator's
    # installed-version provider, and production READY requires a matched
    # version that is explicitly allowlisted — currently only 0.5.1 (see
    # core/version_admission.py).
    #
    # The installed-version probe is a bounded blocking subprocess (5 s), so
    # production runs it on a worker instead of on the GUI thread. The pool is
    # injected here rather than defaulted inside the coordinator so the
    # deterministic tests keep their synchronous behaviour.
    coordinator = CoreStartupCoordinator(
        settings=settings,
        clock=clock,
        api_contract=VersionAdmission.NOT_EVALUATED,
        probe_pool=QThreadPool.globalInstance(),
    )
    controller = StartupController(coordinator)

    window = MainWindow(interpreter_settings=settings, controller=controller)

    exit_code = [0]

    def _on_shutdown_finished(state: object) -> None:
        exit_code[0] = shutdown_exit_code(state)
        app.quit()

    controller.shutdown_finished.connect(_on_shutdown_finished)

    window.show()
    app.exec()
    return exit_code[0]


if __name__ == "__main__":
    raise SystemExit(main())
