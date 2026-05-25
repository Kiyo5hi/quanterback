from __future__ import annotations

from typing import Protocol

from quanterback.domain.state import SystemState


class SystemStateService(Protocol):
    def get_current(self) -> SystemState: ...
    def set(self, mode: str, reason: str, actor: str) -> None: ...
