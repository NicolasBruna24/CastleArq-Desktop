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

"""User interface package for CastleArq Desktop.

Contains the main window and any future screens. UI code must not directly own
HTTP, process-management, or persistence responsibilities.
"""

from __future__ import annotations

from castlearq_desktop.ui.main_window import MainWindow

__all__ = ["MainWindow"]
