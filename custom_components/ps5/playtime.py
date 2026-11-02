"""Play time today and this week, counted locally from the console's power state (and the game from the PSN cloud).

Every status poll (5 s) adds the time since the previous poll to the counters, if the console was on during it.
A poll interval that crosses midnight is split at midnight, so the old day keeps its share and the new day starts
with the rest. Days and weeks (from Monday) follow HA's local time zone. The counters live in a Store, so they
survive restarts; the time HA itself was down is never counted (nobody knows whether the console was on then).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import PS5Coordinator

STORAGE_VERSION = 1
SAVE_DELAY = 60
# Longer pauses between two polls (HA blocked, clock jumped) are not counted: the state in between is unknown
MAX_GAP = timedelta(minutes=10)


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.playtime.{entry_id}"


async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
    await Store[dict[str, Any]](hass, STORAGE_VERSION, storage_key(entry_id)).async_remove()


def next_midnight(moment: datetime) -> datetime:
    return dt_util.start_of_local_day(dt_util.as_local(moment).date() + timedelta(days=1))


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass(slots=True)
class Period:
    """Seconds the console was on / a game was running in one day or week, plus seconds per game."""

    start: date
    on: float = 0.0
    in_game: float = 0.0
    games: dict[str, float] = field(default_factory=dict)

    def add(self, seconds: float, on: bool, game: str | None) -> None:
        if on:
            self.on += seconds
        if game:
            self.in_game += seconds
            self.games[game] = self.games.get(game, 0.0) + seconds

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "on": self.on, "in_game": self.in_game, "games": self.games}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Period:
        return cls(
            start=date.fromisoformat(data["start"]),
            on=float(data.get("on", 0)),
            in_game=float(data.get("in_game", 0)),
            games={str(k): float(v) for k, v in (data.get("games") or {}).items()},
        )


def minutes(seconds: float) -> int:
    return int(seconds // 60)


def games_minutes(games: dict[str, float]) -> dict[str, int]:
    """Games with at least one full minute, longest first."""
    result = {title: minutes(s) for title, s in sorted(games.items(), key=lambda g: -g[1])}
    return {title: m for title, m in result.items() if m > 0}


class PlayTimeTracker:
    def __init__(self, hass: HomeAssistant, coordinator: PS5Coordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        key = storage_key(coordinator.config_entry.entry_id)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, key)
        today = dt_util.now().date()
        self.day = Period(today)
        self.week = Period(week_start(today))
        # Previous poll: time, console on, running game. None after a (re)start: nothing to count yet
        self._last: tuple[datetime, bool, str | None] | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._save_due: datetime | None = None

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self.day = Period.from_dict(stored["day"])
            self.week = Period.from_dict(stored["week"])
        self.sample(dt_util.utcnow())
        entry = self.coordinator.config_entry
        # Via the entry, so the listeners also go away when the setup fails after this point
        entry.async_on_unload(self.coordinator.async_add_listener(self._on_poll))
        # Midnight also without a poll, so "today" drops to 0 right away
        entry.async_on_unload(async_track_time_change(self.hass, self._on_midnight, hour=0, minute=0, second=0))

    async def async_unload(self) -> None:
        await self._store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {"day": self.day.as_dict(), "week": self.week.as_dict()}

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> Callable[[], None]:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    def _current(self) -> tuple[bool, str | None]:
        """Console on (really measured, not the expected state of a transition) and the running game, if known."""
        coordinator = self.coordinator
        if coordinator.last_actual != "on":
            return False, None
        status = coordinator.last_status
        game = status.app_name if status else None
        psn = coordinator.psn
        if not game and psn is not None and psn.last_update_success and psn.data and psn.data.playing_on_ps5:
            game = psn.data.game.get("titleName") or None
        return True, game

    @callback
    def _on_poll(self) -> None:
        self.sample(dt_util.utcnow())

    @callback
    def _on_midnight(self, now: datetime) -> None:
        self.sample(dt_util.utcnow())

    def sample(self, now: datetime) -> None:
        """Count the time since the previous poll with the state of that poll, then remember the current state."""
        if self._last is not None:
            since, was_on, was_game = self._last
            if timedelta(0) < now - since <= MAX_GAP:
                cursor = since
                while (midnight := next_midnight(cursor)) <= now:
                    self._add(midnight - cursor, was_on, was_game)
                    self._roll(midnight)
                    cursor = midnight
                self._add(now - cursor, was_on, was_game)
        self._roll(now)
        on, game = self._current()
        self._last = (now, on, game)
        # async_delay_save restarts its timer on every call; with a poll every 5 s it would never write.
        # A clock that jumped back must not hold the next save off until it catches up.
        due = self._save_due
        if due is None or now >= due or due - now > timedelta(seconds=SAVE_DELAY):
            self._store.async_delay_save(self._data, SAVE_DELAY)
            self._save_due = now + timedelta(seconds=SAVE_DELAY)
        for update in list(self._listeners):
            update()

    def _add(self, span: timedelta, on: bool, game: str | None) -> None:
        seconds = span.total_seconds()
        self.day.add(seconds, on, game)
        self.week.add(seconds, on, game)

    def _roll(self, now: datetime) -> None:
        """Start a new day / week, but only forwards: a clock that is behind (no NTP yet at boot) keeps the counts."""
        today = dt_util.as_local(now).date()
        if today > self.day.start:
            self.day = Period(today)
        if week_start(today) > self.week.start:
            self.week = Period(week_start(today))
