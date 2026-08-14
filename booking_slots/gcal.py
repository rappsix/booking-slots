"""Синхронизация с Google Calendar: токены, события, занятость.

Модуль отвечает за три вещи и намеренно не умеет ничего больше:

* следит за жизненным циклом OAuth-токена — когда его пора обновить и что
  делать, если сервер ответил ошибкой;
* собирает тело запроса на создание события из доменного объекта;
* превращает ответ freeBusy в интервалы занятости, которые понимает
  :mod:`booking_slots.slots`.

Сети здесь нет. HTTP-вызов передаётся снаружи как обычная функция
``transport(method, url, **kwargs) -> Response``. Благодаря этому логика
синхронизации тестируется без моков библиотек, без реального Google и без
единого сетевого запроса — достаточно подставить функцию, возвращающую
заранее заданный ответ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Минимально достаточный набор прав: читать занятость и управлять только
# теми событиями, которые создало само приложение.
REQUIRED_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
)

# Обновляем токен заранее: если он истекает через минуту, запрос всё равно
# может не успеть дойти.
REFRESH_MARGIN = timedelta(minutes=5)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4


class CalendarError(Exception):
    """Ошибка обращения к Google Calendar, которую не имеет смысла повторять."""


class TokenExpired(CalendarError):
    """Refresh-token отозван: нужна повторная авторизация владельцем календаря."""


@dataclass
class CalendarToken:
    """OAuth-токен одного подключённого календаря.

    ``refresh_token`` Google выдаёт ровно один раз — при первом согласии.
    Поэтому при обновлении пары он не перезаписывается пустым значением:
    потерять его означает заставить пользователя проходить OAuth заново.
    """

    access_token: str
    refresh_token: str
    expires_at: datetime
    calendar_id: str = "primary"
    scopes: tuple[str, ...] = REQUIRED_SCOPES

    def needs_refresh(self, now: datetime) -> bool:
        return self.expires_at - REFRESH_MARGIN <= now

    def has_required_scopes(self) -> bool:
        return set(REQUIRED_SCOPES).issubset(self.scopes)

    def refreshed(self, payload: dict, now: datetime) -> "CalendarToken":
        """Новый токен из ответа Google, со сохранением refresh_token."""
        return CalendarToken(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token") or self.refresh_token,
            expires_at=now + timedelta(seconds=int(payload.get("expires_in", 3600))),
            calendar_id=self.calendar_id,
            scopes=tuple(payload.get("scope", " ".join(self.scopes)).split()),
        )


@dataclass(frozen=True)
class EventDraft:
    """Визит, который нужно отразить в календаре мастера."""

    summary: str
    start: datetime
    end: datetime
    timezone: str
    description: str = ""
    # Свой идентификатор визита. Google принимает его как ключ идемпотентности:
    # повторная отправка того же события не создаёт дубль.
    idempotency_key: str | None = None

    def to_payload(self) -> dict:
        payload: dict = {
            "summary": self.summary,
            "start": {"dateTime": self.start.isoformat(), "timeZone": self.timezone},
            "end": {"dateTime": self.end.isoformat(), "timeZone": self.timezone},
        }
        if self.description:
            payload["description"] = self.description
        if self.idempotency_key:
            payload["id"] = self.idempotency_key
        return payload


@dataclass
class SyncResult:
    """Что получилось после попытки синхронизации."""

    ok: bool
    event_id: str | None = None
    token: CalendarToken | None = None
    error: str | None = None
    attempts: int = 1


def should_retry(status: int, attempt: int) -> bool:
    """Повторять ли запрос: только временные ошибки и не бесконечно."""
    return status in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS


def backoff_delay(attempt: int, base: float = 1.0) -> float:
    """Экспоненциальная пауза между попытками: 1, 2, 4, 8 секунд."""
    return base * (2 ** (attempt - 1))


def refresh_request(token: CalendarToken, client_id: str, client_secret: str) -> dict:
    """Тело запроса на обновление access-токена."""
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": token.refresh_token,
        "grant_type": "refresh_token",
    }


def busy_intervals(freebusy: dict, calendar_id: str = "primary") -> list[tuple[datetime, datetime]]:
    """Разобрать ответ freeBusy в список занятых интервалов.

    Google возвращает время в RFC 3339 с зоной. Внутри ядра расчёта слотов
    время наивное и в зоне мастера, поэтому здесь смещение отбрасывается
    после приведения: конвертация делается один раз, на границе.
    """
    calendars = freebusy.get("calendars", {})
    entries = calendars.get(calendar_id, {}).get("busy", [])
    result: list[tuple[datetime, datetime]] = []
    for entry in entries:
        start = _parse_rfc3339(entry["start"])
        end = _parse_rfc3339(entry["end"])
        if end > start:
            result.append((start, end))
    return merge_intervals(result)


def merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Слить пересекающиеся и соприкасающиеся интервалы в непрерывные блоки.

    Google отдаёт занятость по каждому событию отдельно, и они могут
    накладываться. Без слияния расчёт слотов сделал бы ту же работу N раз.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _parse_rfc3339(value: str) -> datetime:
    """«2026-03-02T10:00:00Z» и «...+03:00» → наивный datetime локальной зоны."""
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone().replace(tzinfo=None)


@dataclass
class CalendarClient:
    """Тонкий клиент поверх переданного транспорта.

    ``transport`` — любая функция ``(method, url, headers, json) -> Response``,
    где Response имеет ``.status_code`` и ``.json()``. В продакшне это httpx,
    в тестах — три строчки заглушки.
    """

    client_id: str
    client_secret: str
    transport: object
    _sleep: object = field(default=None, repr=False)

    def ensure_token(self, token: CalendarToken, now: datetime) -> CalendarToken:
        """Обновить токен, если он вот-вот истечёт. Иначе вернуть как есть."""
        if not token.needs_refresh(now):
            return token
        response = self.transport(
            "POST",
            TOKEN_URL,
            json=refresh_request(token, self.client_id, self.client_secret),
        )
        if response.status_code == 400:
            # Google отвечает 400 invalid_grant, когда доступ отозвали руками
            # в настройках Google-аккаунта. Повторять бессмысленно.
            raise TokenExpired("refresh token отозван, нужна повторная авторизация")
        if response.status_code != 200:
            raise CalendarError(f"обновление токена не удалось: {response.status_code}")
        return token.refreshed(response.json(), now)

    def create_event(
        self,
        token: CalendarToken,
        draft: EventDraft,
        *,
        now: datetime,
    ) -> SyncResult:
        """Создать событие, обновив токен при необходимости и повторив при 5xx."""
        try:
            token = self.ensure_token(token, now)
        except CalendarError as exc:
            return SyncResult(ok=False, error=str(exc), token=None)

        url = f"{CALENDAR_API}/calendars/{token.calendar_id}/events"
        headers = {"Authorization": f"Bearer {token.access_token}"}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self.transport("POST", url, headers=headers, json=draft.to_payload())
            if response.status_code in (200, 201):
                return SyncResult(
                    ok=True,
                    event_id=response.json().get("id"),
                    token=token,
                    attempts=attempt,
                )
            if response.status_code == 409:
                # Событие с таким id уже есть — значит визит уже синхронизирован.
                # Для вызывающего это успех, а не ошибка.
                return SyncResult(
                    ok=True,
                    event_id=draft.idempotency_key,
                    token=token,
                    attempts=attempt,
                )
            if not should_retry(response.status_code, attempt):
                return SyncResult(
                    ok=False,
                    token=token,
                    error=f"календарь ответил {response.status_code}",
                    attempts=attempt,
                )
            if self._sleep is not None:
                self._sleep(backoff_delay(attempt))

        return SyncResult(ok=False, token=token, error="исчерпаны попытки", attempts=MAX_ATTEMPTS)
