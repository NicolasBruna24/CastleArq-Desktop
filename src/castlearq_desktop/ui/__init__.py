"""User interface package for CastleArq Desktop.

Contains the main window and any future screens. UI code must not directly own
HTTP, process-management, or persistence responsibilities.
"""

from __future__ import annotations

from castlearq_desktop.ui.main_window import MainWindow

__all__ = ["MainWindow"]
