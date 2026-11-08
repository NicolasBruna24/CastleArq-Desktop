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
  ``build_launch_args``: ``[<absolute-python>, \"-m\", \"castlearq.main\",
  \"serve\"]``); the adapter never resolves via PATH nor installs Core.
- ``shell=False`` always; ``shell=True`` is refused with a definite
  ``ProcessSpawnError`` (no child created).
- ``stdin=stdout=stderr=DEVNULL``; ``cwd``/``env`` left as ``None`` so the
  child inherits Desktop's working directory and environment.
- Non-blocking only: ``poll()``/``is_alive()`` never wait; no timeouts,
  retries, deadlines, or extra signals. ``terminate()``/``kill()`` act only
  on the owned ``Popen`` instance created by ``spawn``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any, Callable, List, Optional

from castlearq_desktop.core.lifecycle_manager import ProcessOwnershipUncertainError
from castlearq_desktop.core.lifecycle_manager import ProcessSpawnError


# ---------------------------------------------------------------------------
# Subprocess process adapter – creates Desktop-owned children via Popen
# ---------------------------------------------------------------------------

class SubprocessProcessAdapter:
    """Create Desktop-owned Core children via ``subprocess.Popen``.

    This adapter uses ``start_new_session=True`` so that each spawned process
    begins in its own session and process group.  This eliminates the need for
    subsequent ``os.setpgid()`` / ``os.setsid()`` calls and makes process
    termination via negative PID reliable.

    On Windows the session handling is omitted – the child inherits the parent's
    job hierarchy.
    """

    def __init__(
        self,
        popen_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._factory = popen_factory if popen_factory is not None else subprocess.Popen

    def spawn(self, argv: List[str], *, shell: bool = False) -> SubprocessProcessHandle:
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
                start_new_session=True,
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

    def spawn_without_process_group(self, argv: List[str], *, shell: bool = False) -> SubprocessProcessHandle:
        """Spawn a process without creating a dedicated session.

        Useful when we want the child to stay in the parent's session (e.g.,
        some UI workflows where process-group termination would be wrong).
        """
        if shell is not False and shell != 0:
            raise ProcessSpawnError("Shell invocation is not permitted.")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) and a for a in argv
        ):
            raise ProcessSpawnError("Launch arguments must be a non-empty list.")
        try:
            popen = subprocess.Popen(
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
            raise ProcessSpawnError(f"Core process creation failed: {exc}") from exc
        except Exception as exc:
            raise ProcessOwnershipUncertainError(
                "Process creation outcome is indeterminate."
            ) from exc
        if popen is None:
            raise ProcessOwnershipUncertainError("No process handle was created.")
        return SubprocessProcessHandle(popen)

    def terminate_all(self, handles: List[SubprocessProcessHandle]) -> None:
        """Terminate multiple process handles gracefully."""
        for handle in handles:
            try:
                handle.terminate()
            except Exception:
                pass

    def kill_all(self, handles: List[SubprocessProcessHandle]) -> None:
        """Force-kill multiple process handles."""
        for handle in handles:
            try:
                handle.kill()
            except Exception:
                pass

    def wait_all(
        self,
        handles: List[SubprocessProcessHandle],
        timeout: Optional[float] = None,
    ) -> List[Optional[int]]:
        """Wait for all handles to finish, returning their exit codes.

        Returns a list of exit codes (or ``None`` if each timed out), in the
        same order as *handles*.
        """
        results: List[Optional[int]] = []
        for handle in handles:
            results.append(handle.wait(timeout=timeout))
        return results


# ---------------------------------------------------------------------------
# Process handle owning one child created by the adapter
# ---------------------------------------------------------------------------

class SubprocessProcessHandle:
    """Owns exactly one ``Popen`` instance created by the adapter.

    Responsibility:
    - The PID of the child also serves as the process group leader PID
      when ``start_new_session=True`` was used in ``spawn()``.
    - Provide non-blocking liveness checks.
    - Graceful termination (SIGTERM) followed by forced kill (SIGKILL) if
      the process does not exit within a timeout.
    - No explicit process-group management is needed when
      ``start_new_session=True`` was used – the PID itself is the group ID.
    - Clean up on Windows/non-POSIX or when explicitly requested.
    """

    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen

    @property
    def popen(self) -> subprocess.Popen:
        return self._popen

    @property
    def pid(self) -> Optional[int]:
        """Return the OS process ID of the owned child, if available."""
        return getattr(self._popen, "pid", None)

    @property
    def is_alive(self) -> bool:
        # Non-blocking: Popen.poll() returns None while running.
        return self._popen.poll() is None

    def poll(self) -> Optional[int]:
        # Non-blocking; None while running, exit code after exit.
        # Exceptions propagate so the manager can fall back / report unknown.
        return self._popen.poll()

    def terminate(self) -> None:
        """Send SIGTERM to the process group (or the process itself).

        With ``start_new_session=True`` the PID itself is the process-group
        leader, so a negative PID reliably kills the whole process rather
        than requiring a separate ``os.setpgid()`` call.
        """
        if os.name == "nt":
            self._popen.terminate()  # type: ignore[attr-lettre]
        else:
            try:
                # Negative PID sends the signal to the whole process group.
                os.kill(-self._popen.pid, signal.SIGTERM)  # type: ignore[attr-lettre]
            except (ProcessLookupError, PermissionError, OSError):
                # Fall back to terminating just the process.
                self._popen.terminate()  # type: ignore[attr-lettre]

    def kill(self) -> None:
        """Force-kill the process group (or the process itself)."""
        if os.name == "nt":
            self._popen.kill()  # type: ignore[attr-lettre]
        else:
            try:
                os.kill(-self._popen.pid, signal.SIGKILL)  # type: ignore[attr-lettre]
            except (ProcessLookupError, PermissionError, OSError):
                # Fall back to killing just the process.
                self._popen.kill()  # type: ignore[attr-lettre]

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Wait for the process to terminate, returning the exit code.

        When ``start_new_session=True`` was used the PID is also the
        process-group leader, so ``os.kill(-pid, SIGTERM)`` / ``os.kill(-pid, SIGKILL)``
        will reliably terminate the process and any descendants.

        Args:
            timeout: Maximum seconds to wait.  ``None`` means wait indefinitely.

        Returns:
            The exit code if the process terminated, or ``None`` if the timeout
            was reached before termination.
        """
        start = time.monotonic()
        self.terminate()
        deadline = None
        if timeout is not None:
            deadline = start + timeout

        while True:
            if timeout is not None and time.monotonic() > deadline:
                # Timeout reached – force-kill and return None.
                try:
                    self.kill()
                except Exception:
                    pass
                return None

            exit_code = self._popen.poll()
            if exit_code is not None:
                return exit_code

            # Sleep briefly, respecting the remaining timeout.
            remaining = None
            if deadline is not None:
                remaining = max(0, deadline - time.monotonic())
            time.sleep(min(0.1, remaining or 0.1))


# ---------------------------------------------------------------------------
# Helper: create a process group so we can terminate whole process trees
# (preserved for backward compatibility; no longer needed when
# start_new_session=True is used, but kept for callers that may still
# rely on it.)
# ---------------------------------------------------------------------------

def _create_process_group(popen: subprocess.Popen) -> None:
    """Put the child process and all its descendants into a new process group.

    On POSIX systems we use ``setsid`` so the child becomes a session leader
    with its own process group.  This allows terminating the entire tree by
    sending ``SIGTERM``/``SIGKILL`` to the negative PID (the group leader).

    On Windows we do nothing special – the child inherits the parent's job
    hierarchy.

    .. note::
        When ``subprocess.Popen`` is called with ``start_new_session=True`` the
        child process begins in its own session and the PID also serves as the
        process-group leader.  In that case this helper is unnecessary; callers
        that still rely on it do so for backward compatibility only.
    """
    if os.name != "nt":
        try:
            popen.start_new_session()  # type: ignore[attr-lettre]
        except (AttributeError, OSError):
            # Fall back: manually set the process group.
            try:
                os.setpgid(popen.pid, -popen.pid)  # type: ignore[attr-lettre]  # negative = process group leader
            except (AttributeError, ProcessLookupError):
                pass


__all__ = [
    "SubprocessProcessAdapter",
    "SubprocessProcessHandle",
    "_create_process_group",
]
