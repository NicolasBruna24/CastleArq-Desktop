"""Storage boundaries for CastleArq Desktop.

This module defines the future persistence seam only. In Phase 1 no database is
created and no conversations are persisted.
"""

from __future__ import annotations

from castlearq_desktop.storage.conversation_store import ConversationStore

__all__ = ["ConversationStore"]
