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

"""Concrete subprocess adapter for the lifecycle manager (ProcessAdapter).

Implements the existing ``ProcessAdapter``/``ProcessHandle`` protocols from
``castlearq_desktop.core.lifecycle_manager`` using ``subprocess.Popen``.

Launch policy (from this task authorization; no prior ADR records stdio/cwd):
- argv comes from the caller (``LifecycleManager`` builds it with
  ``build_launch_args``: ``[<absolute-python>, "-m", "castlearq.main",
  "serve"]``); the adapter never resolves via PATH nor installs Core.
- ``shell=False`` always; ``shell=True`` is refused with a definite
  ``ProcessSpawnError`` (no child created).
- ``stdin=stdout=stderr=DEVNULL``; ``cwd``/``env`` left as ``None`` so the
  child inherits Desktop's working directory and environment.
- Non-blocking only: ``poll()``/``is_alive()`` never wait; no timeouts,
  retries, deadlines, or extra signals. ``terminate()``/``kill()`` act only
  on the owned ``Popen`` instance created by ``spawn``.
"""

from __future__ import annotations

import subprocess
from typing import Any, Callable, List, Optional

from castlearq_desktop.core.lifecycle_manager import ProcessOwnershipUncertainError
from castlearq_desktop.core.lifecycle_manager import ProcessSpawnError


class SubprocessProcessHandle:
    """Owns exactly one ``Popen`` instance created by the adapter."""

    def __init__(self, popen: Any) -> None:
        self._popen = popen

    @property
    def popen(self) -> Any:
        return self._popen

    @property
    def pid(self) -> Optional[int]:
        """Return the OS process ID of the owned child, if available."""
        return getattr(self._popen, "pid", None)

    def is_alive(self) -> bool:
        # Non-blocking: Popen.poll() returns None while running.
        return self._popen.poll() is None

    def poll(self) -> Optional[int]:
        # Non-blocking; None while running, exit code after exit.
        # Exceptions propagate so the manager can fall back / report unknown.
        return self._popen.poll()

    def terminate(self) -> None:
        self._popen.terminate()

    def kill(self) -> None:
        self._popen.kill()


class SubprocessProcessAdapter:
    """Create Desktop-owned Core children via ``subprocess.Popen``."""

    def __init__(
        self,
        popen_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._factory = popen_factory if popen_factory is not None else subprocess.Popen

    def spawn(self, argv: List[str], *, shell: bool) -> SubprocessProcessHandle:
        if shell is not False and shell != 0:
            raise ProcessSpawnError("Shell invocation is not permitted.")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) and a for a in argv
        ):
            raise ProcessSpawnError("Launch arguments must be a non-empty list.")
        try:
            popen = self._factory(
                list(argv),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except ProcessSpawnError:
            raise
        except ProcessOwnershipUncertainError:
            raise
        except (FileNotFoundError, PermissionError, OSError) as exc:
            # Definite failure: OS guarantees no child was created.
            raise ProcessSpawnError(f"Core process creation failed: {exc}") from exc
        except Exception as exc:
            # Indeterminate: factory did not guarantee no child was created.
            raise ProcessOwnershipUncertainError(
                "Process creation outcome is indeterminate."
            ) from exc
        if popen is None:
            raise ProcessOwnershipUncertainError("No process handle was created.")
        return SubprocessProcessHandle(popen)


__all__ = ["SubprocessProcessAdapter", "SubprocessProcessHandle"]
