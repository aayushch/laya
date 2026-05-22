# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Abstract base class for egress execution backends."""

from __future__ import annotations

import abc

from laya.egress.models import EgressRequest, EgressResult


class EgressBackend(abc.ABC):
    """Interface for egress execution backends.

    Backends handle the actual communication with external platforms.
    The egress router selects the appropriate backend for each request.
    """

    @abc.abstractmethod
    async def execute(
        self, request: EgressRequest, credentials: dict
    ) -> EgressResult:
        """Execute an outbound action using this backend.

        Args:
            request: The egress request describing what to do.
            credentials: Platform credentials resolved by the connection broker.

        Returns:
            EgressResult with success/failure and response data.
        """

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Check if the backend is reachable and operational."""

    @abc.abstractmethod
    def supports(self, platform: str, action_type: str) -> bool:
        """Check if this backend can handle the given platform + action."""
