"""WP1 — Interpreter path configuration (DEC-C / ADR-0001 Amendment A1).

Persists the *explicitly user-selected* Python interpreter path under the
single approved QSettings identity and key. The store is deliberately dumb: it
performs no PATH lookup, no environment inspection, no interpreter discovery,
no migration, and no validation-result persistence. A missing selection stays
``None`` (unconfigured) until the user explicitly chooses a path.

The real-settings factory imports PySide6 lazily so this module stays importable
without Qt; tests inject a duck-typed settings object instead.
"""

from __future__ import annotations

import os
from typing import Optional

from castlearq_desktop.core.contracts.settings import (
    APPLICATION_NAME,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
    PYTHON_INTERPRETER_PATH_KEY,
)


class InterpreterSettingsStore:
    """Load/save the single approved interpreter-path setting.

    *settings* is any object exposing the ``QSettings`` value surface:
    ``value(key, defaultValue=None)``, ``setValue(key, value)``, ``remove(key)``
    and ``sync()``. Tests inject an in-memory fake; production injects a real
    ``QSettings`` via :func:`create_interpreter_settings`.
    """

    def __init__(self, settings: object) -> None:
        self._settings = settings

    def load(self) -> Optional[str]:
        """Return the saved interpreter path, or ``None`` when unconfigured.

        No fallback discovery of any kind is performed: what is stored is what
        is returned.
        """
        value = self._settings.value(  # type: ignore[attr-defined]
            PYTHON_INTERPRETER_PATH_KEY, defaultValue=None
        )
        if value is None:
            return None
        path = str(value)
        return path or None

    def save(self, path: str) -> None:
        """Persist *path* as the explicit selection.

        Only non-empty absolute paths are accepted; anything else raises
        ``ValueError`` before any write occurs. Re-saving replaces the previous
        selection (explicit replacement).
        """
        if not isinstance(path, str) or not path.strip():
            raise ValueError("An interpreter path must be a non-empty string.")
        if not os.path.isabs(path):
            raise ValueError(
                "An interpreter path must be absolute; no PATH lookup is performed."
            )
        self._settings.setValue(PYTHON_INTERPRETER_PATH_KEY, path)  # type: ignore[attr-defined]
        self._settings.sync()  # type: ignore[attr-defined]

    def clear(self) -> None:
        """Forget the explicit selection (back to unconfigured)."""
        self._settings.remove(PYTHON_INTERPRETER_PATH_KEY)  # type: ignore[attr-defined]
        self._settings.sync()  # type: ignore[attr-defined]


def create_interpreter_settings() -> InterpreterSettingsStore:
    """Build a store backed by a real ``QSettings`` using the approved identity.

    The approved DEC-C identity triple is applied as far as the Qt API permits:
    the organization name and application name are passed to the supported
    ``QSettings(organization, application)`` constructor — Qt 6's ``QSettings``
    exposes no organization-domain API — and the organization domain is applied
    through ``QCoreApplication.setOrganizationDomain``, the only Qt surface that
    accepts it (the same call ``create_application`` makes at startup). Nothing
    is written during construction. Only the single approved key is ever read or
    written by the returned store.
    """
    from PySide6.QtCore import QCoreApplication, QSettings  # lazy: Qt stays optional

    # Set the domain first: it is a QCoreApplication-level identity in Qt 6, so
    # any platform path derivation that consults it observes it immediately.
    QCoreApplication.setOrganizationDomain(ORGANIZATION_DOMAIN)
    settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
    return InterpreterSettingsStore(settings)


__all__ = [
    "InterpreterSettingsStore",
    "create_interpreter_settings",
]
