from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


class TimeProvider(ABC):
    @abstractmethod
    def now(self) -> datetime: ...


class SystemTimeProvider(TimeProvider):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeTimeProvider(TimeProvider):
    """Controllable clock for eval/chaos scenarios, so SLA windows (hours/days) can
    be crossed deterministically without real wall-clock waits."""

    def __init__(self, start: datetime | None = None):
        self._current = start or datetime.now(timezone.utc)

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        self._current += delta
