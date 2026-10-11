"""Core integration boundaries for CastleArq Desktop.

The HTTP client and process manager remain Phase 1 seams that perform no real
work. Interpreter configuration and validation are implemented (WP1): the
settings store persists only the approved interpreter path key, and the
validation executor runs the approved A1 diagnostic without ever importing or
launching CastleArq Core.
"""

from __future__ import annotations

from castlearq_desktop.core.http_client import CoreHttpClient, HttpClientError
from castlearq_desktop.core.interpreter_settings import (
    InterpreterSettingsStore,
    create_interpreter_settings,
)
from castlearq_desktop.core.interpreter_validator import (
    DIAGNOSTIC_SOURCE,
    build_validation_command,
    validate_python_interpreter,
)
from castlearq_desktop.core.process_manager import CoreProcessManager

__all__ = [
    "CoreHttpClient",
    "HttpClientError",
    "CoreProcessManager",
    "DIAGNOSTIC_SOURCE",
    "InterpreterSettingsStore",
    "build_validation_command",
    "create_interpreter_settings",
    "validate_python_interpreter",
]
