"""Conversation persistence boundary for CastleArq Desktop.

Phase 1 defines this interface only. No SQLite database is created and no
conversations are read or written. Persistence will be implemented in a later
phase.
"""

from __future__ import annotations

from typing import Any


class ConversationStore:
    """Store that will persist conversations to a local database.

    In Phase 1 this class is a boundary placeholder. Every operation raises
    :class:`NotImplementedError` so that no database file is ever created and no
    conversation data is written.
    """

    def __init__(self, database_path: str | None = None) -> None:
        # Stored for later use; no database is opened or created here.
        self._database_path = database_path

    @property
    def database_path(self) -> str | None:
        """Return the configured database path, if any."""
        return self._database_path

    def save_conversation(self, conversation_id: str, messages: Any) -> None:
        """Persist a conversation.

        Not implemented in Phase 1; intentionally writes nothing.
        """
        raise NotImplementedError(
            "Conversation persistence is not implemented in Phase 1."
        )

    def load_conversation(self, conversation_id: str) -> Any:
        """Load a persisted conversation.

        Not implemented in Phase 1; intentionally reads nothing.
        """
        raise NotImplementedError(
            "Conversation persistence is not implemented in Phase 1."
        )
