"""Расчёт свободных слотов записи.

Ядро логики онлайн-записи: из рабочего графика, уже занятых визитов,
отпусков и параметров услуги получить список моментов времени, на которые
клиент может записаться.

Учитывается:

* недельный и сменный график мастера;
* отпуска на весь день и перерывы внутри дня;
* существующие визиты с их длительностью;
* буфер уборки после визита — время, которое нельзя занять следующей записью;
* шаг сетки: слоты предлагаются каждые N минут;
* минимальный запас на сегодня: нельзя записаться «через пять минут»;
* временное удержание слотов за клиентами из листа ожидания;
* визит из нескольких услуг подряд — суммарная длительность одним блоком.

Все datetime — наивные и в локальной зоне мастера. Приведение к его зоне
делается на границе приложения, до вызова этих функций: внутри ядра нет
ни одного обращения к «сейчас» без явной передачи параметра, поэтому
логика полностью детерминирована и тестируема.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .schedule import Schedule
from .timeoff import TimeOff, busy_intervals, is_day_off


@dataclass(frozen=True)
class Service:
    """Услуга: сколько занимает и сколько времени нужно на уборку после."""

    name: str
    duration_min: int
    cleanup_min: int = 0

    @property
    def total_min(self) -> int:
        return self.duration_min + self.cleanup_min


@dataclass(frozen=True)
class Booking:
    """Уже существующая запись, занимающая время мастера."""

    start: datetime
    duration_min: int
    cleanup_min: int = 0

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_min + self.cleanup_min)


@dataclass(frozen=True)
class Hold:
    """Слот, временно удержанный за клиентом из листа ожидания.

    Когда освобождается окно, оно сначала предлагается тому, кто стоит
    в очереди, и на время ``expires_at`` не показывается остальным.
    Так очередь остаётся честной: место не уводят у того, кто ждал.
    """

    start: datetime
    duration_min: int
    expires_at: datetime
    client_id: int | None = None

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_min)

    def is_active(self, now: datetime) -> bool:
        return self.expires_at > now


@dataclass(frozen=True)
class SlotRules:
    """Настройки выдачи слотов."""

    step_min: int = 15
    min_lead_min: int = 60
    horizon_days: int = 30


def _overlaps(
    start: datetime,
    end: datetime,
    intervals: list[tuple[datetime, datetime]],
) -> bool:
    return any(start < busy_end and busy_start < end for busy_start, busy_end in intervals)


def _occupied(
    day: date,
    bookings: list[Booking],
    time_off: list[TimeOff],
    holds: list[Hold],
    now: datetime,
    for_client_id: int | None,
) -> list[tuple[datetime, datetime]]:
    """Собрать все занятые интервалы дня в одном списке."""
    occupied = busy_intervals(time_off, day)

    for booking in bookings:
        if booking.start.date() == day:
            occupied.append((booking.start, booking.end))

    for hold in holds:
        if hold.start.date() != day or not hold.is_active(now):
            continue
        # Клиент, за которым закреплено удержание, видит свой слот свободным.
        if for_client_id is not None and hold.client_id == for_client_id:
            continue
        occupied.append((hold.start, hold.end))

    return occupied


def available_slots(
    day: date,
    schedule: Schedule,
    service: Service,
    *,
    now: datetime,
    bookings: list[Booking] | None = None,
    time_off: list[TimeOff] | None = None,
    holds: list[Hold] | None = None,
    rules: SlotRules | None = None,
    for_client_id: int | None = None,
) -> list[datetime]:
    """Свободные слоты на конкретный день.

    Возвращает моменты начала визита. Слот попадает в результат, только
    если услуга вместе с уборкой целиком помещается в рабочее окно и не
    пересекается ни с одним занятым интервалом.
    """
    bookings = bookings or []
    time_off = time_off or []
    holds = holds or []
    rules = rules or SlotRules()

    if day < now.date() or is_day_off(time_off, day):
        return []

    windows = schedule.windows_for(day)
    if not windows:
        return []

    occupied = _occupied(day, bookings, time_off, holds, now, for_client_id)
    earliest = now + timedelta(minutes=rules.min_lead_min)
    step = timedelta(minutes=rules.step_min)
    need = timedelta(minutes=service.total_min)
    visible = timedelta(minutes=service.duration_min)

    slots: list[datetime] = []
    for window in windows:
        window_start, window_end = window.bounds(day)
        cursor = window_start
        # Окно ограничивает саму услугу: уборка после последнего визита дня
        # может выходить за конец смены, это нормально.
        while cursor + visible <= window_end:
            if cursor >= earliest and not _overlaps(cursor, cursor + need, occupied):
                slots.append(cursor)
            cursor += step

    return slots


def visit_slots(
    day: date,
    schedule: Schedule,
    services: list[Service],
    *,
    now: datetime,
    **kwargs,
) -> list[datetime]:
    """Слоты для визита из нескольких услуг подряд.

    Услуги выполняются одним блоком: суммируется длительность всех, а
    уборка берётся только от последней — между услугами внутри визита
    мастеру убираться не нужно.
    """
    if not services:
        return []

    duration = sum(s.duration_min for s in services)
    combined = Service(
        name=" + ".join(s.name for s in services),
        duration_min=duration,
        cleanup_min=services[-1].cleanup_min,
    )
    return available_slots(day, schedule, combined, now=now, **kwargs)


def available_dates(
    schedule: Schedule,
    service: Service,
    *,
    now: datetime,
    **kwargs,
) -> list[date]:
    """Дни в пределах горизонта, где есть хотя бы один свободный слот.

    Нужно, чтобы в календаре подсвечивать только те даты, куда реально
    можно записаться, и не заставлять клиента тыкать в пустые дни.
    """
    rules: SlotRules = kwargs.get("rules") or SlotRules()
    result: list[date] = []
    for offset in range(rules.horizon_days):
        day = now.date() + timedelta(days=offset)
        if available_slots(day, schedule, service, now=now, **kwargs):
            result.append(day)
    return result


def next_slot(
    schedule: Schedule,
    service: Service,
    *,
    now: datetime,
    **kwargs,
) -> datetime | None:
    """Ближайшее свободное время — для кнопки «Записаться на ближайшее»."""
    rules: SlotRules = kwargs.get("rules") or SlotRules()
    for offset in range(rules.horizon_days):
        day = now.date() + timedelta(days=offset)
        slots = available_slots(day, schedule, service, now=now, **kwargs)
        if slots:
            return slots[0]
    return None
