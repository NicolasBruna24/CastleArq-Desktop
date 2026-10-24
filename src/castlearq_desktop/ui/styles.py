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

"""Shared visual tokens for CastleArq Desktop.

This module defines a small, reusable set of visual constants and helpers
used by the Runtime / Diagnóstico page. It is intentionally minimal: no
custom framework, no global QSS, no external dependencies.

The palette follows an Apple-inspired direction: clarity, consistency,
hierarchy and simplicity. Surfaces are light and neutral; color is used
to communicate state, never as decoration.

Only the Runtime page consumes these tokens in this phase. Other views
are untouched.
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QWidget

# ---------------------------------------------------------------------------
# Color tokens (light, neutral surfaces — Apple-inspired)
# ---------------------------------------------------------------------------

COLOR_BACKGROUND = "#F5F5F7"       # window background
COLOR_SURFACE = "#FFFFFF"          # card / group surface
COLOR_SURFACE_ALT = "#F0F0F2"      # alternate subtle surface
COLOR_BORDER = "#D2D2D7"          # subtle separator
COLOR_TEXT_PRIMARY = "#1D1D1F"     # primary text
COLOR_TEXT_SECONDARY = "#6E6E73"   # secondary / caption text
COLOR_ACCENT = "#0071E3"          # accent (links, focus)

# State colors — used sparingly, always paired with text
COLOR_STATE_INFO = "#0071E3"      # informational
COLOR_STATE_SUCCESS = "#248A3D"   # ready / verified
COLOR_STATE_WARNING = "#BF5000"   # pending / degraded
COLOR_STATE_ERROR = "#D70015"     # failed / error

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------

FONT_FAMILY_UI = "system-ui, -apple-system, 'Helvetica Neue', sans-serif"
FONT_FAMILY_MONO = "'SF Mono', 'Menlo', 'Consolas', monospace"

FONT_SIZE_CAPTION = 11
FONT_SIZE_BODY = 13
FONT_SIZE_TITLE = 17

# ---------------------------------------------------------------------------
# Spacing and radii
# ---------------------------------------------------------------------------

SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

RADIUS_SM = 6
RADIUS_MD = 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_font(size: int, bold: bool = False, mono: bool = False) -> QFont:
    """Return a QFont with the given size and weight.

    ``mono=True`` selects the generic monospace family, used only for
    technical data (PIDs, exit codes) where it improves readability.
    Non-mono fonts use Qt's default application font (no family override).
    """
    font = QFont()
    if mono:
        font.setFamily("Monospace")
    font.setPointSize(size)
    font.setBold(bold)
    return font


def apply_runtime_style(
    title_label: QLabel,
    value_labels: list[QLabel],
    technical_labels: set[QLabel] | None = None,
    page: QWidget | None = None,
) -> None:
    """Apply the shared visual tokens to the Runtime page widgets.

    Only affects the widgets passed in; does not touch any other view or
    set a global stylesheet on QApplication.

    ``technical_labels`` (subset of ``value_labels``) receive the monospace
    font; all other value labels use Qt's default UI font.

    ``page`` (optional) receives an explicit background color so the
    Runtime page does not depend on an inherited (possibly dark) theme.
    """
    if technical_labels is None:
        technical_labels = set()

    # Title
    title_label.setFont(make_font(FONT_SIZE_TITLE, bold=True))
    title_label.setStyleSheet(
        f"color: {COLOR_TEXT_PRIMARY}; margin-bottom: {SPACING_SM}px;"
    )

    # Value labels
    for label in value_labels:
        is_technical = label in technical_labels
        label.setFont(make_font(FONT_SIZE_BODY, mono=is_technical))
        label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; "
            f"padding: {SPACING_XS}px 0; "
            f"line-height: 1.4;"
        )
        label.setWordWrap(True)

    # Page background (isolated to Runtime only)
    if page is not None:
        page.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")


__all__ = [
    "COLOR_BACKGROUND",
    "COLOR_SURFACE",
    "COLOR_SURFACE_ALT",
    "COLOR_BORDER",
    "COLOR_TEXT_PRIMARY",
    "COLOR_TEXT_SECONDARY",
    "COLOR_ACCENT",
    "COLOR_STATE_INFO",
    "COLOR_STATE_SUCCESS",
    "COLOR_STATE_WARNING",
    "COLOR_STATE_ERROR",
    "FONT_FAMILY_UI",
    "FONT_FAMILY_MONO",
    "FONT_SIZE_CAPTION",
    "FONT_SIZE_BODY",
    "FONT_SIZE_TITLE",
    "SPACING_XS",
    "SPACING_SM",
    "SPACING_MD",
    "SPACING_LG",
    "SPACING_XL",
    "RADIUS_SM",
    "RADIUS_MD",
    "make_font",
    "apply_runtime_style",
]
