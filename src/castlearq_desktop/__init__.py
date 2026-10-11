"""CastleArq Desktop application package.

Phase 1 provides only a minimal PySide6 application shell. Core integration
(HTTP communication, Core process management, and conversation persistence) is
intentionally not implemented yet; the corresponding modules define boundaries
only.
"""

from __future__ import annotations

# Human-readable application name shown in the window title and taskbar.
APP_NAME = "CastleArq Desktop"
# Organization name used for QSettings scoping (harmless placeholder for now).
ORGANIZATION_NAME = "CastleArq"
# Reverse-DNS style application identifier used by Qt where required.
ORGANIZATION_DOMAIN = "castlearq.local"

__all__ = ["APP_NAME", "ORGANIZATION_NAME", "ORGANIZATION_DOMAIN"]
