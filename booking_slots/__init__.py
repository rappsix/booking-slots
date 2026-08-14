"""booking-slots — расчёт свободных слотов онлайн-записи.

Извлечено из продакшн-кода SaaS-платформы Zapisly и очищено от
инфраструктуры: ни базы данных, ни ORM, ни внешних зависимостей —
только stdlib и чистые функции, которые легко тестировать.
"""

from .schedule import ShiftSchedule, WeeklySchedule, WorkWindow
from .slots import (
    Booking,
    Hold,
    Service,
    SlotRules,
    available_dates,
    available_slots,
    next_slot,
    visit_slots,
)
from .timeoff import TimeOff

__all__ = [
    "WorkWindow",
    "WeeklySchedule",
    "ShiftSchedule",
    "TimeOff",
    "Service",
    "Booking",
    "Hold",
    "SlotRules",
    "available_slots",
    "visit_slots",
    "available_dates",
    "next_slot",
]

__version__ = "1.0.0"
