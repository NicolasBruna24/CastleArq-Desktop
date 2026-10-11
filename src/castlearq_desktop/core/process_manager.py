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
