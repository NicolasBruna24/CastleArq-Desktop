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

"""Core process-management boundary for CastleArq Desktop.

Phase 1 defines this interface only. No method starts, stops, signals, or
otherwise manages a CastleArq Core process. Lifecycle handling will be
implemented in a later phase.
"""

from __future__ import annotations


class CoreProcessManager:
    """Manager that will own the lifecycle of the CastleArq Core process.

    In Phase 1 this class is a boundary placeholder. Starting and stopping are
    intentionally unimplemented so that no process is ever launched.
    """

    def __init__(self, executable: str | None = None) -> None:
        # Stored for later use; no process is created here.
        self._executable = executable

    @property
    def executable(self) -> str | None:
        """Return the configured Core executable path, if any."""
        return self._executable

    def start(self) -> None:
        """Start the Core process.

        Not implemented in Phase 1; intentionally starts no process.
        """
        raise NotImplementedError(
            "Starting the Core process is not implemented in Phase 1."
        )

    def stop(self) -> None:
        """Stop the Core process.

        Not implemented in Phase 1; intentionally stops no process.
        """
        raise NotImplementedError(
            "Stopping the Core process is not implemented in Phase 1."
        )

    def is_running(self) -> bool:
        """Report whether Core is running.

        Not implemented in Phase 1; always returns ``False`` because no process
        is ever managed.
        """
        return False
