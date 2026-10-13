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
