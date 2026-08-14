from datetime import date, datetime, timedelta

import pytest

from booking_slots import (
    Booking,
    Hold,
    Service,
    ShiftSchedule,
    SlotRules,
    TimeOff,
    WeeklySchedule,
    WorkWindow,
    available_dates,
    available_slots,
    next_slot,
    visit_slots,
)

MONDAY = date(2026, 3, 2)
NOW = datetime(2026, 3, 1, 12, 0)

FULL_DAY = WeeklySchedule(
    days={
        "mon": [WorkWindow("10:00", "18:00")],
        "tue": [WorkWindow("10:00", "18:00")],
        "wed": [WorkWindow("10:00", "14:00"), WorkWindow("16:00", "20:00")],
    }
)

HAIRCUT = Service("Стрижка", duration_min=60, cleanup_min=15)
RULES = SlotRules(step_min=30, min_lead_min=60, horizon_days=14)


def test_empty_on_day_off():
    assert available_slots(date(2026, 3, 8), FULL_DAY, HAIRCUT, now=NOW, rules=RULES) == []


def test_slots_fit_inside_window():
    slots = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, rules=RULES)
    assert slots[0] == datetime(2026, 3, 2, 10, 0)
    # Последний слот начинается так, чтобы сама услуга закончилась к 18:00.
    assert slots[-1] == datetime(2026, 3, 2, 17, 0)


def test_booking_blocks_overlapping_slots():
    booking = Booking(datetime(2026, 3, 2, 12, 0), duration_min=60, cleanup_min=15)
    slots = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, bookings=[booking], rules=RULES)
    assert datetime(2026, 3, 2, 12, 0) not in slots
    assert datetime(2026, 3, 2, 11, 30) not in slots  # пересечение по длительности
    assert datetime(2026, 3, 2, 13, 30) in slots  # после уборки


def test_cleanup_time_is_reserved():
    booking = Booking(datetime(2026, 3, 2, 10, 0), duration_min=60, cleanup_min=15)
    slots = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, bookings=[booking], rules=RULES)
    # 11:00 занято уборкой до 11:15, ближайший слот по сетке — 11:30.
    assert datetime(2026, 3, 2, 11, 0) not in slots
    assert datetime(2026, 3, 2, 11, 30) in slots


def test_lunch_break_excluded():
    lunch = TimeOff(MONDAY, MONDAY, "13:00", "14:00")
    slots = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, time_off=[lunch], rules=RULES)
    assert datetime(2026, 3, 2, 12, 30) not in slots
    assert datetime(2026, 3, 2, 14, 0) in slots


def test_vacation_closes_whole_day():
    vacation = TimeOff(date(2026, 3, 1), date(2026, 3, 5))
    assert available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, time_off=[vacation], rules=RULES) == []


def test_min_lead_time_hides_immediate_slots():
    now = datetime(2026, 3, 2, 10, 5)
    slots = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=now, rules=RULES)
    assert all(slot >= now + timedelta(minutes=60) for slot in slots)


def test_two_windows_in_one_day():
    wednesday = date(2026, 3, 4)
    slots = available_slots(wednesday, FULL_DAY, HAIRCUT, now=NOW, rules=RULES)
    assert datetime(2026, 3, 4, 13, 0) in slots
    assert datetime(2026, 3, 4, 14, 30) not in slots  # перерыв между окнами
    assert datetime(2026, 3, 4, 16, 0) in slots


def test_hold_hides_slot_from_others_but_not_from_owner():
    hold = Hold(
        start=datetime(2026, 3, 2, 12, 0),
        duration_min=60,
        expires_at=datetime(2026, 3, 1, 13, 0),
        client_id=42,
    )
    for_others = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, holds=[hold], rules=RULES)
    for_owner = available_slots(
        MONDAY, FULL_DAY, HAIRCUT, now=NOW, holds=[hold], rules=RULES, for_client_id=42
    )
    assert datetime(2026, 3, 2, 12, 0) not in for_others
    assert datetime(2026, 3, 2, 12, 0) in for_owner


def test_expired_hold_releases_slot():
    hold = Hold(
        start=datetime(2026, 3, 2, 12, 0),
        duration_min=60,
        expires_at=datetime(2026, 3, 1, 11, 0),  # истёк раньше now
    )
    slots = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, holds=[hold], rules=RULES)
    assert datetime(2026, 3, 2, 12, 0) in slots


def test_shift_schedule_two_through_two():
    schedule = ShiftSchedule(
        anchor=date(2026, 3, 2),
        work_days=2,
        rest_days=2,
        windows=[WorkWindow("09:00", "21:00")],
    )
    assert schedule.is_workday(date(2026, 3, 2))
    assert schedule.is_workday(date(2026, 3, 3))
    assert not schedule.is_workday(date(2026, 3, 4))
    assert not schedule.is_workday(date(2026, 3, 5))
    assert schedule.is_workday(date(2026, 3, 6))
    # График корректен и до опорной даты.
    assert not schedule.is_workday(date(2026, 3, 1))
    assert schedule.is_workday(date(2026, 2, 27))


def test_visit_of_several_services_needs_one_block():
    manicure = Service("Маникюр", duration_min=90, cleanup_min=10)
    slots = visit_slots(MONDAY, FULL_DAY, [HAIRCUT, manicure], now=NOW, rules=RULES)
    # 60 + 90 = 150 минут работы, уборка только после последней услуги.
    assert slots[-1] == datetime(2026, 3, 2, 15, 30)


def test_available_dates_skips_empty_days():
    vacation = TimeOff(date(2026, 3, 2), date(2026, 3, 3))
    days = available_dates(FULL_DAY, HAIRCUT, now=NOW, time_off=[vacation], rules=RULES)
    assert date(2026, 3, 2) not in days
    assert date(2026, 3, 4) in days


def test_next_slot_returns_earliest():
    assert next_slot(FULL_DAY, HAIRCUT, now=NOW, rules=RULES) == datetime(2026, 3, 2, 10, 0)


def test_next_slot_none_when_fully_booked():
    vacation = TimeOff(date(2026, 3, 1), date(2026, 4, 1))
    assert next_slot(FULL_DAY, HAIRCUT, now=NOW, time_off=[vacation], rules=RULES) is None


@pytest.mark.parametrize(
    "step,expected_first_two",
    [
        (15, [datetime(2026, 3, 2, 10, 0), datetime(2026, 3, 2, 10, 15)]),
        (60, [datetime(2026, 3, 2, 10, 0), datetime(2026, 3, 2, 11, 0)]),
    ],
)
def test_grid_step(step, expected_first_two):
    rules = SlotRules(step_min=step, min_lead_min=60, horizon_days=7)
    slots = available_slots(MONDAY, FULL_DAY, HAIRCUT, now=NOW, rules=rules)
    assert slots[:2] == expected_first_two
