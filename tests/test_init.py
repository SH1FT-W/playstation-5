"""Entitäten, Zustände, Aufwecken/Ruhemodus mit Übergangszustand, Diagnose."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ps5.const import DOMAIN
from custom_components.ps5.diagnostics import async_get_config_entry_diagnostics
from custom_components.ps5.protocol import PS5AuthError, PS5Error

from .conftest import Console, status

MP = "media_player.ps5_123"
SW = "switch.ps5_123_power"
STATUS = "sensor.ps5_123_status"
GAME = "sensor.ps5_123_game"


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_states(hass: HomeAssistant, setup: MockConfigEntry, console: Console) -> None:
    assert hass.states.get(MP).state == "off"
    assert hass.states.get(SW).state == STATE_OFF
    assert hass.states.get(STATUS).state == "rest"
    console.answer = status(True, ("ASTRO BOT", "PPSA01325"))
    await _refresh(hass, setup)
    mp = hass.states.get(MP)
    assert mp.state == "playing" and mp.attributes["media_title"] == "ASTRO BOT"
    assert mp.attributes["app_id"] == "PPSA01325"
    assert hass.states.get(GAME).state == "ASTRO BOT"
    assert hass.states.get(SW).state == STATE_ON
    console.answer = status(True)
    await _refresh(hass, setup)
    assert hass.states.get(MP).state == "on"
    console.answer = None  # komplett aus / nicht im Netz
    await _refresh(hass, setup)
    assert hass.states.get(MP).state == "off"
    assert hass.states.get(STATUS).state == "off"
    assert hass.states.get(GAME).state == "unknown"


async def test_turn_on_shows_transition(hass: HomeAssistant, setup: MockConfigEntry, console: Console) -> None:
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: SW}, blocking=True)
    assert console.calls == ["wakeup"]
    assert hass.states.get(SW).state == STATE_ON  # sofort, obwohl die PS5 noch Ruhemodus meldet
    assert hass.states.get(STATUS).attributes["changing"] is True
    await _refresh(hass, setup)
    assert hass.states.get(SW).state == STATE_ON  # bleibt, bis die PS5 nachzieht
    console.answer = status(True)
    await _refresh(hass, setup)
    assert hass.states.get(STATUS).attributes["changing"] is False
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: SW}, blocking=True)
    assert console.calls == ["wakeup"]  # schon an: nichts senden


async def test_turn_off(hass: HomeAssistant, setup: MockConfigEntry, console: Console) -> None:
    console.answer = status(True)
    await _refresh(hass, setup)
    await hass.services.async_call("media_player", "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert console.calls == ["standby"]
    assert hass.states.get(MP).state == "off"
    console.answer = None  # manche Konsolen verschwinden im Ruhemodus ganz: gilt auch als erledigt
    await _refresh(hass, setup)
    assert hass.states.get(STATUS).attributes["changing"] is False


async def test_transition_times_out(
    hass: HomeAssistant, setup: MockConfigEntry, console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.ps5.coordinator.TRANSITION_SECONDS", -1)
    await hass.services.async_call("media_player", "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    await _refresh(hass, setup)  # PS5 blieb im Ruhemodus → echter Zustand zählt
    assert hass.states.get(MP).state == "off"


async def test_standby_transient_error_retries(
    hass: HomeAssistant, setup: MockConfigEntry, console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belegt/Verbindung weg (z. B. kurz nach dem Aufwachen): kein Fehler, HA versucht es im Hintergrund nochmal."""
    monkeypatch.setattr("custom_components.ps5.coordinator.RETRY_DELAYS", (0, 0))
    console.answer = status(True)
    await _refresh(hass, setup)
    console.standby_error = PS5Error("Connection lost")
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SW}, blocking=True)
    assert hass.states.get(SW).state == STATE_OFF  # zeigt den Zielzustand
    await hass.async_block_till_done(wait_background_tasks=True)
    assert console.calls.count("standby") == 3  # 1 + 2 Wiederholungen, alle fehlgeschlagen


async def test_standby_retry_succeeds(
    hass: HomeAssistant, setup: MockConfigEntry, console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.ps5.coordinator.RETRY_DELAYS", (0, 0))
    console.answer = status(True)
    await _refresh(hass, setup)
    console.standby_error = PS5Error("belegt")
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SW}, blocking=True)
    console.standby_error = None  # zweiter Versuch klappt
    await hass.async_block_till_done(wait_background_tasks=True)
    assert console.calls == ["standby", "standby"]


async def test_standby_not_linked_starts_reauth(hass: HomeAssistant, setup: MockConfigEntry, console: Console) -> None:
    console.answer = status(True)
    await _refresh(hass, setup)
    console.standby_error = PS5AuthError("80108b02")
    with pytest.raises(HomeAssistantError, match="no longer knows"):
        await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SW}, blocking=True)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_wakeup_os_error(hass: HomeAssistant, setup: MockConfigEntry, console: Console) -> None:
    async def broken(host: str, regist_key: str) -> None:
        raise OSError("Netz weg")

    from unittest.mock import patch

    with (
        patch("custom_components.ps5.coordinator.async_wakeup", broken),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: SW}, blocking=True)


async def test_diagnostics(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(
        setup, data={**setup.data, "psn_account_id": "9876543210987654321", "psn_refresh_token": "geheimes-token"}
    )
    data = await async_get_config_entry_diagnostics(hass, setup)
    text = str(data)
    assert data["state"] == "rest"
    for secret in (
        "6162636431323334",
        "1234567890123456789",
        "9876543210987654321",
        "geheimes-token",
        "192.0.2.50",
        "AABBCCDDEEFF",
    ):
        assert secret not in text


async def test_unload(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(setup.entry_id)


async def test_transition_lasts_while_retrying(
    hass: HomeAssistant, setup: MockConfigEntry, console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die 45 s Übergang sind kürzer als die Wiederholungskette (210 s): solange sie läuft, bleibt „aus“ stehen."""
    monkeypatch.setattr("custom_components.ps5.coordinator.TRANSITION_SECONDS", -1)
    monkeypatch.setattr("custom_components.ps5.coordinator.RETRY_DELAYS", (0,))
    console.answer = status(True)
    await _refresh(hass, setup)
    console.standby_error = PS5Error("belegt")
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SW}, blocking=True)
    console.standby_gate = asyncio.Event()  # die Wiederholung bleibt im Senden hängen
    await asyncio.sleep(0)
    assert setup.runtime_data.retrying
    await _refresh(hass, setup)  # Übergang abgelaufen, PS5 meldet weiter „an“
    assert hass.states.get(SW).state == STATE_OFF
    assert hass.states.get(STATUS).attributes["changing"] is True
    console.standby_error = None
    console.standby_gate.set()  # Wiederholung klappt
    await hass.async_block_till_done(wait_background_tasks=True)
    assert console.calls == ["standby", "standby"]
    console.answer = status(False)
    await _refresh(hass, setup)
    assert hass.states.get(STATUS).attributes["changing"] is False


async def test_all_retries_fail_ends_transition(
    hass: HomeAssistant, setup: MockConfigEntry, console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.ps5.coordinator.RETRY_DELAYS", (0,))
    console.answer = status(True)
    await _refresh(hass, setup)
    console.standby_error = PS5Error("belegt")
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SW}, blocking=True)
    assert hass.states.get(SW).state == STATE_OFF
    await hass.async_block_till_done(wait_background_tasks=True)
    assert console.calls == ["standby", "standby"]
    # alle Versuche durch: der echte Zustand zählt wieder, ohne auf die 45 s zu warten
    assert hass.states.get(SW).state == STATE_ON
    assert hass.states.get(STATUS).attributes["changing"] is False


async def test_second_turn_off_replaces_retry_chain(
    hass: HomeAssistant, setup: MockConfigEntry, console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zweimal „aus“ darf nur eine Wiederholungskette laufen lassen."""
    monkeypatch.setattr("custom_components.ps5.coordinator.RETRY_DELAYS", (5, 5))
    console.answer = status(True)
    await _refresh(hass, setup)
    console.standby_error = PS5Error("belegt")
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SW}, blocking=True)
    coordinator = setup.runtime_data
    first = coordinator._retry_task
    assert first is not None and coordinator.retrying
    # PS5 meldet sich weiter als „an“ und der Übergang ist (aus Sicht der Entität) vorbei
    coordinator._expected = None
    await _refresh(hass, setup)
    assert hass.states.get(SW).state == STATE_ON
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SW}, blocking=True)
    await asyncio.sleep(0)
    assert first.cancelled()
    assert coordinator.retrying and coordinator._retry_task is not first
    assert console.calls == ["standby", "standby"]
    # Entladen räumt die laufende Kette ab
    assert await hass.config_entries.async_unload(setup.entry_id)
    await hass.async_block_till_done()
    assert not coordinator.retrying


async def test_retry_stops_when_console_fully_off(
    hass: HomeAssistant, setup: MockConfigEntry, console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Am Netzschalter ganz ausgeschaltet (keine DDP-Antwort mehr): keine weiteren Ruhemodus-Versuche."""
    monkeypatch.setattr("custom_components.ps5.coordinator.RETRY_DELAYS", (0, 0))
    console.answer = status(True)
    await _refresh(hass, setup)
    console.answer = None  # PS5 antwortet nicht mehr, last_status meldet aber noch „an“
    await _refresh(hass, setup)
    await setup.runtime_data._standby_retry()
    assert console.calls == []
