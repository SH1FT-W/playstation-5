"""Play time today / this week: only while on, split at midnight, new week on Monday, survives restarts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ps5.const import DOMAIN
from custom_components.ps5.firmware import INFO_URL
from custom_components.ps5.playtime import storage_key

from .conftest import ENTRY_DATA, MAC, Console, status
from .test_cloud import CLOUD_DATA, INFO_PAGE, mock_psn

TZ = ZoneInfo("Europe/Berlin")
TODAY = "sensor.ps5_123_play_time_today"
WEEK = "sensor.ps5_123_play_time_this_week"
IN_GAME = "sensor.ps5_123_play_time_in_game_today"
FC26 = "EA SPORTS FC 26"


@pytest.fixture(autouse=True)
async def berlin(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    # Moving the clock also starts the firmware check: answer it instead of going online
    aioclient_mock.get(INFO_URL.format(locale="en-us"), text=INFO_PAGE)


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=TZ)  # type: ignore[misc]


async def _setup(hass: HomeAssistant, data: dict[str, Any] = ENTRY_DATA) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="PS5-123", unique_id=MAC, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, entry: MockConfigEntry, minutes: float, step: float = 5
) -> None:
    """Let time pass in poll steps (the real poll runs every 5 s; bigger steps count the same way). 0 = poll now."""
    left = minutes
    while True:
        freezer.tick(timedelta(minutes=min(step, left)))
        left -= step
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        if left <= 0:
            return


def _value(hass: HomeAssistant, entity_id: str) -> int:
    return int(hass.states.get(entity_id).state)


async def test_counts_only_while_on(hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(at(2026, 11, 3, 10, 0))  # Tuesday
    entry = await _setup(hass)  # console in rest mode
    await _advance(hass, freezer, entry, 30)
    assert _value(hass, TODAY) == 0
    console.answer = status(True)
    await _advance(hass, freezer, entry, 0)  # the poll sees the console on
    await _advance(hass, freezer, entry, 45)
    console.answer = None  # fully off
    await _advance(hass, freezer, entry, 0)
    await _advance(hass, freezer, entry, 20)
    today = hass.states.get(TODAY)
    assert today.state == "45"
    assert today.attributes["unit_of_measurement"] == "min"
    assert today.attributes["device_class"] == "duration"
    assert today.attributes["state_class"] == "total"
    assert today.attributes["games"] == {}
    assert "in_game" not in today.attributes  # without the PSN cloud
    assert _value(hass, WEEK) == 45
    assert hass.states.get(WEEK).attributes["last_reset"] == "2026-11-02T00:00:00+01:00"  # Monday 00:00 Berlin
    assert hass.states.get(IN_GAME) is None


async def test_rest_mode_transition_counts_real_state(
    hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory
) -> None:
    """After 'turn off' the switch already shows rest mode, but the console runs on until it really sleeps."""
    freezer.move_to(at(2026, 11, 3, 10, 0))
    console.answer = status(True)
    entry = await _setup(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.ps5_123_power"}, blocking=True)
    await _advance(hass, freezer, entry, 10)
    assert _value(hass, TODAY) == 10


async def test_midnight_splits_running_session(
    hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(2026, 11, 3, 23, 42, 30))  # Tuesday, polls at :47:30, :52:30, :57:30, 00:02:30 …
    console.answer = status(True)
    entry = await _setup(hass)
    await _advance(hass, freezer, entry, 15)
    assert _value(hass, TODAY) == 15
    await _advance(hass, freezer, entry, 25)  # until 00:22:30
    today = hass.states.get(TODAY)
    assert today.state == "22"  # 22.5 min since midnight, the 17.5 min before belong to Tuesday
    assert today.attributes["last_reset"] == "2026-11-04T00:00:00+01:00"
    assert _value(hass, WEEK) == 40


async def test_midnight_without_poll(hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory) -> None:
    """'Today' drops to 0 at midnight even if no poll comes right then."""
    freezer.move_to(at(2026, 11, 3, 23, 30))
    console.answer = status(True)
    entry = await _setup(hass)
    await _advance(hass, freezer, entry, 28)  # 23:58
    assert _value(hass, TODAY) == 28
    await entry.runtime_data.async_shutdown()  # no more polls: only the midnight timer can reset
    freezer.move_to(at(2026, 11, 4, 0, 0, 0))
    async_fire_time_changed(hass, at(2026, 11, 4, 0, 0, 0))
    await hass.async_block_till_done()
    assert _value(hass, TODAY) == 0
    assert _value(hass, WEEK) == 30


async def test_new_week_on_monday(hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(at(2026, 11, 8, 23, 0))  # Sunday
    console.answer = status(True)
    entry = await _setup(hass)
    await _advance(hass, freezer, entry, 50)
    assert _value(hass, WEEK) == 50
    await _advance(hass, freezer, entry, 30)  # Monday 00:20
    assert _value(hass, TODAY) == 20
    week = hass.states.get(WEEK)
    assert week.state == "20"
    assert week.attributes["last_reset"] == "2026-11-09T00:00:00+01:00"


async def test_long_gap_not_counted(hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory) -> None:
    """HA blocked or clock jumped: an hour without polls is unknown, not play time."""
    freezer.move_to(at(2026, 11, 3, 10, 0))
    console.answer = status(True)
    entry = await _setup(hass)
    await _advance(hass, freezer, entry, 60, step=60)
    assert _value(hass, TODAY) == 0
    await _advance(hass, freezer, entry, 5)
    assert _value(hass, TODAY) == 5


async def test_restart_keeps_counts(
    hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    freezer.move_to(at(2026, 11, 3, 18, 0))
    console.answer = status(True)
    entry = await _setup(hass)
    await _advance(hass, freezer, entry, 30)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[storage_key(entry.entry_id)]["data"]["day"]["on"] == 30 * 60
    freezer.tick(timedelta(hours=2))  # HA down for 2 h: not counted, even though the console stays on
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _value(hass, TODAY) == 30
    await _advance(hass, freezer, entry, 10)
    assert _value(hass, TODAY) == 40
    assert _value(hass, WEEK) == 40


async def test_restart_on_a_later_day(
    hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="PS5-123", unique_id=MAC, data=ENTRY_DATA)
    hass_storage[storage_key(entry.entry_id)] = {
        "version": 1,
        "minor_version": 1,
        "key": storage_key(entry.entry_id),
        "data": {
            "day": {"start": "2026-11-03", "on": 3000.0, "in_game": 0.0, "games": {}},
            "week": {"start": "2026-11-02", "on": 7200.0, "in_game": 0.0, "games": {}},
        },
    }
    freezer.move_to(at(2026, 11, 4, 9, 0))  # Wednesday, same week
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _value(hass, TODAY) == 0
    assert _value(hass, WEEK) == 120
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert storage_key(entry.entry_id) not in hass_storage


async def test_in_game_with_psn_cloud(
    hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    freezer.move_to(at(2026, 11, 3, 16, 0))
    mock_psn(aioclient_mock)
    entry = await _setup(hass, CLOUD_DATA)  # rest mode: PSN still reports the game, but it does not count
    await _advance(hass, freezer, entry, 10)
    assert _value(hass, IN_GAME) == 0
    console.answer = status(True)
    await _advance(hass, freezer, entry, 0)  # the poll sees the console on
    await _advance(hass, freezer, entry, 25)
    in_game = hass.states.get(IN_GAME)
    assert in_game.state == "25"
    assert in_game.attributes["games"] == {FC26: 25}
    today = hass.states.get(TODAY)
    assert today.state == "25" and today.attributes["in_game"] == 25
    assert today.attributes["games"] == {FC26: 25}
    mock_psn(aioclient_mock, presence={"basicPresence": {"availability": "availableToPlay"}})  # back in the menu
    await entry.runtime_data.psn.async_refresh()
    await _advance(hass, freezer, entry, 0)
    await _advance(hass, freezer, entry, 10)
    assert _value(hass, TODAY) == 35
    assert _value(hass, IN_GAME) == 25
    assert hass.states.get(WEEK).attributes["in_game"] == 25


async def test_clock_behind_keeps_counts(
    hass: HomeAssistant, console: Console, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    """HA boots before NTP and thinks it is last Sunday: the stored week must survive until the clock is right."""
    entry = MockConfigEntry(domain=DOMAIN, title="PS5-123", unique_id=MAC, data=ENTRY_DATA)
    hass_storage[storage_key(entry.entry_id)] = {
        "version": 1,
        "minor_version": 1,
        "key": storage_key(entry.entry_id),
        "data": {
            "day": {"start": "2026-11-04", "on": 600.0, "in_game": 0.0, "games": {}},
            "week": {"start": "2026-11-02", "on": 7200.0, "in_game": 0.0, "games": {}},
        },
    }
    freezer.move_to(at(2026, 11, 1, 12, 0))  # Sunday of the previous week
    console.answer = status(True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.move_to(at(2026, 11, 4, 20, 0))  # NTP: Wednesday evening (the jump itself is not counted)
    await _advance(hass, freezer, entry, 0)
    assert _value(hass, WEEK) == 120
    await _advance(hass, freezer, entry, 5)
    assert _value(hass, WEEK) == 125
    assert _value(hass, TODAY) == 15
