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

"""HTTP adapter boundary for communicating with CastleArq Core.

Phase 1 defines this interface only. No methods perform real network requests.
The concrete transport (approved as ``httpx`` for a later phase) and the request
behaviour will be implemented in a later work package.

The default request timeout is the DEC-E maximum of 4 seconds. It is inert here:
no request is ever made, so changing it has no behavioural effect in WP0.
"""

from __future__ import annotations

from typing import Any

from castlearq_desktop.core.contracts import MAX_REQUEST_TIMEOUT_SECONDS


class HttpClientError(Exception):
    """Base error type for HTTP adapter failures (reserved for later phases)."""


class CoreHttpClient:
    """Adapter that will talk to the CastleArq Core HTTP API.

    In WP0 this class remains a boundary placeholder. Every operation raises
    :class:`NotImplementedError` so that no network activity can occur
    accidentally. Automatic transport retries are disabled by policy (DEC-E).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = MAX_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        # Stored for later use; no connection is opened here.
        self._base_url = base_url
        self._timeout = timeout

    @property
    def base_url(self) -> str | None:
        """Return the configured Core base URL, if any."""
        return self._base_url

    def get(self, path: str, **kwargs: Any) -> Any:
        """Perform a GET request against Core.

        Not implemented in Phase 1; intentionally makes no network request.
        """
        raise NotImplementedError(
            "HTTP communication with Core is not implemented in Phase 1."
        )

    def post(self, path: str, **kwargs: Any) -> Any:
        """Perform a POST request against Core.

        Not implemented in Phase 1; intentionally makes no network request.
        """
        raise NotImplementedError(
            "HTTP communication with Core is not implemented in Phase 1."
        )
