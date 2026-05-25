from __future__ import annotations

from dataclasses import dataclass, field

from quanterback.domain.events import NotificationEvent


@dataclass
class FakeNotifier:
    pushed: list[NotificationEvent] = field(default_factory=list)

    def push(self, event: NotificationEvent) -> None:
        self.pushed.append(event)
