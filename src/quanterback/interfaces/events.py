from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from quanterback.domain.events import ControlCommand, ScanEvent


class EventSource(Protocol):
    def stream(self) -> Iterable[ScanEvent]: ...


class ControlChannel(Protocol):
    def listen(self) -> Iterable[ControlCommand]: ...
