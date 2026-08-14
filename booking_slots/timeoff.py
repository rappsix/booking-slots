"""Отпуска, отгулы и перерывы внутри дня."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .schedule import at


@dataclass(frozen=True)
class TimeOff:
    """Период недоступности мастера.

    Если ``start_time`` и ``end_time`` не заданы — день закрыт целиком
    (отпуск, больничный). Если заданы — это перерыв внутри дня: обед,
    поездка к поставщику, личные дела.
    """

    start_date: date
    end_date: date
    start_time: str | None = None
    end_time: str | None = None

    def covers(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date

    @property
    def is_full_day(self) -> bool:
        return self.start_time is None or self.end_time is None


def is_day_off(entries: list[TimeOff], day: date) -> bool:
    """Закрыт ли день целиком хотя бы одной записью."""
    return any(e.covers(day) and e.is_full_day for e in entries)


def busy_intervals(entries: list[TimeOff], day: date) -> list[tuple[datetime, datetime]]:
    """Интервалы внутри дня, в которые мастер недоступен."""
    result: list[tuple[datetime, datetime]] = []
    for entry in entries:
        if not entry.covers(day) or entry.is_full_day:
            continue
        result.append((at(day, entry.start_time), at(day, entry.end_time)))
    return result
