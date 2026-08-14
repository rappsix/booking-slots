"""booking-slots — расчёт свободных слотов онлайн-записи.

Извлечено из продакшн-кода SaaS-платформы Zapisly и очищено от
инфраструктуры: ни базы данных, ни ORM, ни внешних зависимостей —
только stdlib и чистые функции, которые легко тестировать.
"""

from .gcal import (
    CalendarClient,
    CalendarError,
    CalendarToken,
    EventDraft,
    SyncResult,
    TokenExpired,
    merge_intervals,
)
from .gcal import busy_intervals as gcal_busy_intervals
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
    # графики и время
    "WorkWindow",
    "WeeklySchedule",
    "ShiftSchedule",
    "TimeOff",
    # расчёт слотов
    "Service",
    "Booking",
    "Hold",
    "SlotRules",
    "available_slots",
    "visit_slots",
    "available_dates",
    "next_slot",
    # синхронизация с Google Calendar
    "CalendarToken",
    "EventDraft",
    "CalendarClient",
    "SyncResult",
    "CalendarError",
    "TokenExpired",
    "gcal_busy_intervals",
    "merge_intervals",
]

__version__ = "1.1.0"
