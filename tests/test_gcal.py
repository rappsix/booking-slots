from datetime import datetime, timedelta

import pytest

from booking_slots.gcal import (
    REQUIRED_SCOPES,
    CalendarClient,
    CalendarError,
    CalendarToken,
    EventDraft,
    TokenExpired,
    backoff_delay,
    busy_intervals,
    merge_intervals,
    should_retry,
)

NOW = datetime(2026, 3, 1, 12, 0)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeTransport:
    """Отдаёт заранее заданные ответы по очереди и запоминает запросы."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return self.responses.pop(0) if self.responses else FakeResponse(500)


def make_token(expires_in_min=60):
    return CalendarToken(
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=NOW + timedelta(minutes=expires_in_min),
        scopes=REQUIRED_SCOPES,
    )


def make_draft():
    return EventDraft(
        summary="Стрижка — Анна",
        start=datetime(2026, 3, 2, 10, 0),
        end=datetime(2026, 3, 2, 11, 0),
        timezone="Europe/Moscow",
        idempotency_key="visit42",
    )


def test_token_needs_refresh_before_actual_expiry():
    assert not make_token(60).needs_refresh(NOW)
    # Запас в пять минут: токен, живущий три минуты, уже считается протухшим.
    assert make_token(3).needs_refresh(NOW)


def test_refresh_keeps_refresh_token_when_google_omits_it():
    token = make_token(1)
    refreshed = token.refreshed({"access_token": "access-2", "expires_in": 3600}, NOW)
    assert refreshed.access_token == "access-2"
    assert refreshed.refresh_token == "refresh-1"
    assert refreshed.expires_at == NOW + timedelta(hours=1)


def test_missing_scope_is_detected():
    token = CalendarToken("a", "r", NOW, scopes=("https://www.googleapis.com/auth/calendar.events",))
    assert not token.has_required_scopes()
    assert make_token().has_required_scopes()


def test_event_payload_carries_timezone_and_idempotency_key():
    payload = make_draft().to_payload()
    assert payload["start"] == {"dateTime": "2026-03-02T10:00:00", "timeZone": "Europe/Moscow"}
    assert payload["id"] == "visit42"
    assert "description" not in payload  # пустое поле не отправляем


def test_busy_intervals_parse_and_merge():
    freebusy = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2026-03-02T10:00:00Z", "end": "2026-03-02T11:00:00Z"},
                    {"start": "2026-03-02T10:30:00Z", "end": "2026-03-02T12:00:00Z"},
                    {"start": "2026-03-02T15:00:00Z", "end": "2026-03-02T16:00:00Z"},
                ]
            }
        }
    }
    result = busy_intervals(freebusy)
    # Первые два накладываются — должны схлопнуться в один блок.
    assert len(result) == 2
    assert result[0][1] - result[0][0] == timedelta(hours=2)


def test_busy_intervals_skips_zero_length_and_empty_calendar():
    freebusy = {
        "calendars": {
            "primary": {"busy": [{"start": "2026-03-02T10:00:00Z", "end": "2026-03-02T10:00:00Z"}]}
        }
    }
    assert busy_intervals(freebusy) == []
    assert busy_intervals({}) == []


def test_merge_intervals_joins_touching_blocks():
    a = (datetime(2026, 3, 2, 10), datetime(2026, 3, 2, 11))
    b = (datetime(2026, 3, 2, 11), datetime(2026, 3, 2, 12))
    assert merge_intervals([b, a]) == [(a[0], b[1])]


def test_retry_policy():
    assert should_retry(503, attempt=1)
    assert not should_retry(503, attempt=4)  # попытки кончились
    assert not should_retry(404, attempt=1)  # не временная ошибка
    assert [backoff_delay(i) for i in (1, 2, 3)] == [1.0, 2.0, 4.0]


def test_create_event_refreshes_expired_token_first():
    transport = FakeTransport(
        FakeResponse(200, {"access_token": "access-2", "expires_in": 3600}),
        FakeResponse(201, {"id": "gcal-1"}),
    )
    client = CalendarClient("cid", "secret", transport)
    result = client.create_event(make_token(1), make_draft(), now=NOW)

    assert result.ok and result.event_id == "gcal-1"
    assert transport.calls[0]["url"].endswith("/token")
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer access-2"


def test_create_event_skips_refresh_for_fresh_token():
    transport = FakeTransport(FakeResponse(201, {"id": "gcal-1"}))
    client = CalendarClient("cid", "secret", transport)
    result = client.create_event(make_token(60), make_draft(), now=NOW)

    assert result.ok
    assert len(transport.calls) == 1  # обновления токена не было


def test_conflict_is_treated_as_success():
    """409 значит, что визит уже был отправлен — дубля быть не должно."""
    transport = FakeTransport(FakeResponse(409))
    client = CalendarClient("cid", "secret", transport)
    result = client.create_event(make_token(60), make_draft(), now=NOW)

    assert result.ok and result.event_id == "visit42"


def test_temporary_error_is_retried_then_succeeds():
    slept = []
    transport = FakeTransport(FakeResponse(503), FakeResponse(503), FakeResponse(201, {"id": "x"}))
    client = CalendarClient("cid", "secret", transport, _sleep=slept.append)
    result = client.create_event(make_token(60), make_draft(), now=NOW)

    assert result.ok and result.attempts == 3
    assert slept == [1.0, 2.0]


def test_permanent_error_is_not_retried():
    transport = FakeTransport(FakeResponse(404))
    client = CalendarClient("cid", "secret", transport)
    result = client.create_event(make_token(60), make_draft(), now=NOW)

    assert not result.ok and result.attempts == 1
    assert "404" in result.error


def test_revoked_refresh_token_raises():
    transport = FakeTransport(FakeResponse(400, {"error": "invalid_grant"}))
    client = CalendarClient("cid", "secret", transport)
    with pytest.raises(TokenExpired):
        client.ensure_token(make_token(1), NOW)


def test_broken_refresh_returns_error_result():
    transport = FakeTransport(FakeResponse(500))
    client = CalendarClient("cid", "secret", transport)
    result = client.create_event(make_token(1), make_draft(), now=NOW)

    assert not result.ok
    assert isinstance(CalendarError("x"), Exception)
