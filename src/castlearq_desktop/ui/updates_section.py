# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Settings Updates section (Stage 3 UI)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from castlearq_desktop.core.update_discovery import (
    CoreUpdateDiscoveryState as S,
)

_CHECKING_TEXT = "Checking for published versions…"
_IDLE_TEXT = "No check performed yet."
_ALREADY_TEXT = "A check is already in progress."
_UNKNOWN = "unknown"


def _fmt(version):
    return version if version else _UNKNOWN


def describe_result(result):
    """Build (status, detail) user-visible texts; never claims installability."""
    state = result.state
    installed = _fmt(result.installed_version)
    latest = _fmt(result.latest_stable)
    admitted = ", ".join(sorted(result.admitted_versions)) or _UNKNOWN
    head = (f"Installed version: {installed}\n"
            f"Latest stable published version: {latest}\n"
            f"Admitted version(s): {admitted}")
    if state is S.LATEST_INSTALLED:
        return ("The installed version is the latest published stable version.",
                head)
    if state is S.NEWER_STABLE_ADMITTED:
        return ("A newer stable version is published and it is admitted "
                "by this Desktop release. Desktop does not install or "
                "download anything; provisioning remains manual.", head)
    if state is S.NEWER_STABLE_NOT_ADMITTED:
        return ("A newer stable version is published, but it is NOT admitted "
                "by this Desktop release. Desktop keeps its current "
                "fail-closed behavior.", head)
    if state is S.INSTALLED_AHEAD:
        return ("The installed version is newer than the latest published "
                "stable version.", head)
    if state is S.INSTALLED_UNKNOWN:
        if result.latest_stable:
            return ("The installed version is unknown (no interpreter "
                    "configured or it could not be determined). "
                    "The published version is shown when available.", head)
        return ("The installed version is unknown and no eligible stable "
                "published version was found.", head)
    if state is S.SEARCH_FAILED:
        reason = ""
        if result.search_failure is not None:
            try:
                reason = str(result.search_failure.user_message)
            except Exception:  # noqa: BLE001 - degrade gracefully
                reason = ""
        detail = head + ("\n" + reason if reason else "")
        if result.latest_stable is None:
            return ("The published-version search failed. No eligible "
                    "stable published version was found.", detail)
        return ("The published-version search failed.", detail)
    return ("No check performed yet.", head)


class UpdatesSection(QWidget):
    """Updates settings section: manual check + three-version display."""

    def __init__(self, controller=None, parent=None):
        super().__init__(parent)
        self._controller = controller
        box = QGroupBox("Updates", self)
        layout = QVBoxLayout(box)
        self._status = QLabel(_IDLE_TEXT, box)
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail = QLabel("", box)
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._button = QPushButton("Check for updates", box)
        self._button.clicked.connect(self._on_check)
        layout.addWidget(self._status)
        layout.addWidget(self._detail)
        layout.addWidget(self._button)
        outer = QVBoxLayout(self)
        outer.addWidget(box)
        self._checking = False
        if self._controller is not None:
            self._controller.check_started.connect(self._on_started)
            self._controller.already_running.connect(self._on_already)
            self._controller.check_finished.connect(self._on_finished)
            self._controller.check_failed.connect(self._on_failed)

    def _on_check(self):
        if self._controller is None:
            return
        self._controller.check()

    def _on_started(self):
        self._checking = True
        self._button.setEnabled(False)
        self._status.setText(_CHECKING_TEXT)

    def _on_already(self):
        self._status.setText(_ALREADY_TEXT + " " + _CHECKING_TEXT
                              if self._checking else _ALREADY_TEXT)

    def _on_finished(self, result):
        self._checking = False
        self._button.setEnabled(True)
        status, detail = describe_result(result)
        self._status.setText(status)
        self._detail.setText(detail)

    def _on_failed(self, message):
        self._checking = False
        self._button.setEnabled(True)
        self._status.setText(message or "The update check failed.")

    def close_section(self):
        if self._controller is not None:
            self._controller.invalidate()


__all__ = ["UpdatesSection", "describe_result"]
