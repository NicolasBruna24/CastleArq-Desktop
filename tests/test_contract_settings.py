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

"""WP0-G contract tests: settings identity consistency with DEC-C.

Asserts the existing Desktop identity matches the approved QSettings identity
and that exactly one settings key is approved. No Qt object is constructed and
no settings file is written.
"""

from __future__ import annotations

import pytest

from castlearq_desktop import (
    APP_NAME,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
)
from castlearq_desktop.core.contracts import (
    APPROVED_SETTING_KEYS,
    APPLICATION_NAME,
    FORBIDDEN_SETTING_KEYS,
    ORGANIZATION_DOMAIN as CONTRACT_ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME as CONTRACT_ORGANIZATION_NAME,
    PYTHON_INTERPRETER_PATH_KEY,
    is_approved_setting_key,
    qsettings_identity,
)


def test_existing_identity_matches_the_approved_organization() -> None:
    """The existing constants already match DEC-C; no change was needed."""
    assert ORGANIZATION_NAME == "CastleArq"
    assert CONTRACT_ORGANIZATION_NAME == "CastleArq"
    assert ORGANIZATION_NAME == CONTRACT_ORGANIZATION_NAME


def test_existing_identity_matches_the_approved_domain() -> None:
    assert ORGANIZATION_DOMAIN == "castlearq.local"
    assert CONTRACT_ORGANIZATION_DOMAIN == "castlearq.local"
    assert ORGANIZATION_DOMAIN == CONTRACT_ORGANIZATION_DOMAIN


def test_existing_identity_matches_the_approved_application() -> None:
    assert APP_NAME == "CastleArq Desktop"
    assert APPLICATION_NAME == "CastleArq Desktop"
    assert APP_NAME == APPLICATION_NAME


def test_qsettings_identity_mapping_is_the_approved_triple() -> None:
    assert qsettings_identity() == {
        "organization": "CastleArq",
        "organization_domain": "castlearq.local",
        "application": "CastleArq Desktop",
    }


def test_the_approved_interpreter_key_is_exact() -> None:
    """DEC-C approves exactly one settings key."""
    assert PYTHON_INTERPRETER_PATH_KEY == "runtime/python_interpreter_path"


def test_exactly_one_setting_key_is_approved() -> None:
    """Persisting anything else would violate DEC-C."""
    assert APPROVED_SETTING_KEYS == {PYTHON_INTERPRETER_PATH_KEY}
    assert len(APPROVED_SETTING_KEYS) == 1


def test_approved_key_recognition() -> None:
    assert is_approved_setting_key("runtime/python_interpreter_path")
    assert not is_approved_setting_key("runtime/other")


@pytest.mark.parametrize("key", sorted(FORBIDDEN_SETTING_KEYS))
def test_forbidden_keys_are_never_approved(key: str) -> None:
    """PIDs, readiness, versions, validation results, and state are not persisted."""
    assert not is_approved_setting_key(key)
    assert key not in APPROVED_SETTING_KEYS


def test_forbidden_and_approved_key_sets_are_disjoint() -> None:
    assert not (FORBIDDEN_SETTING_KEYS & APPROVED_SETTING_KEYS)
