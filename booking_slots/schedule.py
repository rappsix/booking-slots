"""Рабочий график мастера: недельный и сменный.

Модуль отвечает на один вопрос: какие интервалы времени мастер работает
в конкретный день. Всё остальное — занятость, услуги, буферы — считается
поверх этого результата в :mod:`booking_slots.slots`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_hhmm(value: str) -> time:
    """Разобрать «09:30» в :class:`datetime.time`.

    Единая точка разбора времени на весь проект: если формат меняется,
    правится только здесь.
    """
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def at(day: date, hhmm: str) -> datetime:
    """Собрать наивный datetime из даты и строки «HH:MM»."""
    return datetime.combine(day, parse_hhmm(hhmm))


@dataclass(frozen=True)
class WorkWindow:
    """Непрерывный интервал работы внутри дня, например 10:00–14:00."""

    start: str
    end: str

    def bounds(self, day: date) -> tuple[datetime, datetime]:
        return at(day, self.start), at(day, self.end)


@dataclass
class WeeklySchedule:
    """Обычный недельный график: для каждого дня недели свой набор окон.

    Пустой список окон означает выходной.
    """

    days: dict[str, list[WorkWindow]] = field(default_factory=dict)

    def windows_for(self, day: date) -> list[WorkWindow]:
        return list(self.days.get(WEEKDAYS[day.weekday()], []))


@dataclass
class ShiftSchedule:
    """Сменный график «work_days рабочих через rest_days выходных».

    ``anchor`` — любой день, который является первым днём рабочей смены.
    Цикл разворачивается в обе стороны от опорной даты: отрицательный
    остаток по модулю в Python уже даёт нужный сдвиг, поэтому график
    одинаково корректно считается и для дат до anchor.
    """

    anchor: date
    work_days: int
    rest_days: int
    windows: list[WorkWindow] = field(default_factory=list)

    def is_workday(self, day: date) -> bool:
        cycle = self.work_days + self.rest_days
        if self.work_days <= 0 or cycle <= 0:
            return False
        return (day - self.anchor).days % cycle < self.work_days

    def windows_for(self, day: date) -> list[WorkWindow]:
        return list(self.windows) if self.is_workday(day) else []


Schedule = WeeklySchedule | ShiftSchedule
