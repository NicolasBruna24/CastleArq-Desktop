"""Entry point for ``python -m castlearq_desktop``."""

from __future__ import annotations

import sys

from castlearq_desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
