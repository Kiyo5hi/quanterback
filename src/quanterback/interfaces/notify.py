from __future__ import annotations

from typing import Protocol

from quanterback.domain.events import NotificationEvent


class Notifier(Protocol):
    def push(self, event: NotificationEvent) -> None:
        """MUST NOT raise. Internal failures are caught and logged."""
        ...
