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

import sys
from typing import List, Optional

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


def main(argv: Optional[List[str]] = None) -> int:
    """Run the application shell and return a process exit code.

    Acts as the composition root: builds the production dependencies and the
    ``StartupController``, injects the controller into the window, and keeps the
    controller alive for the whole run (ADR-0002 D7). Window close is routed
    through the controller, which coordinates a non-blocking shutdown; the event
    loop is allowed to end only after a terminal shutdown outcome, at which
    point a ``SHUTDOWN_FAILED`` outcome is translated into a non-zero exit code.
    """
    app = create_application(argv)

    settings = create_interpreter_settings()
    clock = MonotonicClock()
    # DEC-A remains fail-closed: no admission source is approved, so readiness
    # cannot succeed until one is (see contracts/readiness.py).
    coordinator = CoreStartupCoordinator(
        settings=settings,
        clock=clock,
        api_contract=VersionAdmission.NOT_EVALUATED,
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
