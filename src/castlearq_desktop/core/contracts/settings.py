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

"""WP0-G — Settings identity contract (DEC-C).

Declares the approved QSettings identity and the single approved settings key as
importable constants so tests can assert them without touching Qt.

No persistence, no PATH or environment discovery, no migration, and no
interpreter-selection UI is implemented here.
"""

from __future__ import annotations

#: Approved QSettings organization name (DEC-C).
ORGANIZATION_NAME = "CastleArq"

#: Approved QSettings organization domain (DEC-C).
ORGANIZATION_DOMAIN = "castlearq.local"

#: Approved QSettings application name (DEC-C).
APPLICATION_NAME = "CastleArq Desktop"

#: The only approved settings key (DEC-C).
PYTHON_INTERPRETER_PATH_KEY = "runtime/python_interpreter_path"

#: Keys that must never be persisted (DEC-C): PIDs, readiness, detected
#: versions, validation results, and process state.
FORBIDDEN_SETTING_KEYS = frozenset(
    {
        "runtime/core_pid",
        "runtime/process_pid",
        "runtime/readiness",
        "runtime/ready",
        "runtime/core_version",
        "runtime/detected_version",
        "runtime/validation_result",
        "runtime/process_state",
    }
)

#: The complete approved key set. Exactly one key is approved.
APPROVED_SETTING_KEYS = frozenset({PYTHON_INTERPRETER_PATH_KEY})


def is_approved_setting_key(key: str) -> bool:
    """Whether *key* is the single approved settings key (DEC-C)."""
    return key in APPROVED_SETTING_KEYS


def qsettings_identity() -> dict:
    """Return the approved QSettings identity as a plain mapping."""
    return {
        "organization": ORGANIZATION_NAME,
        "organization_domain": ORGANIZATION_DOMAIN,
        "application": APPLICATION_NAME,
    }


__all__ = [
    "APPROVED_SETTING_KEYS",
    "APPLICATION_NAME",
    "FORBIDDEN_SETTING_KEYS",
    "ORGANIZATION_DOMAIN",
    "ORGANIZATION_NAME",
    "PYTHON_INTERPRETER_PATH_KEY",
    "is_approved_setting_key",
    "qsettings_identity",
]
