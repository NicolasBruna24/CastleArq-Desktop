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
