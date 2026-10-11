"""Application bootstrap and Qt application lifecycle.

This module owns creation of the ``QApplication`` and the main window and the
top-level ``exec`` loop. It performs no Core integration work: no HTTP requests,
no Core process management, and no persistence.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PySide6.QtWidgets import QApplication

from castlearq_desktop import APP_NAME, ORGANIZATION_DOMAIN, ORGANIZATION_NAME
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


def main(argv: Optional[List[str]] = None) -> int:
    """Run the CastleArq Desktop application shell and return an exit code."""
    app = create_application(argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
