"""Fragt die PS5 alle 5 s per DDP ab. Keine Antwort heißt „aus“ (nicht „Fehler“)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_REGIST_KEY, CONF_RP_KEY, DOMAIN, TRANSITION_SECONDS, UPDATE_INTERVAL
from .protocol import PS5AuthError, PS5Error, PS5Status, async_get_status, async_standby, async_wakeup

if TYPE_CHECKING:
    from .cloud import PSNCoordinator

_LOGGER = logging.getLogger(__name__)

type PS5ConfigEntry = ConfigEntry[PS5Coordinator]

# Ruhemodus nach einem Fehlschlag erneut versuchen (Sekunden zwischen den Versuchen)
RETRY_DELAYS = (20, 40, 60, 90)

STATE_ON = "on"
STATE_REST = "rest"
STATE_OFF = "off"


@dataclass(frozen=True, slots=True)
class PS5Data:
    state: str  # on / rest / off
    status: PS5Status | None
    transition: bool = False  # Befehl gesendet, PS5 hat ihn noch nicht bestätigt

    @property
    def is_on(self) -> bool:
        return self.state == STATE_ON

    @property
    def app_name(self) -> str | None:
        return self.status.app_name if self.status and self.is_on else None

    @property
    def title_id(self) -> str | None:
        return self.status.title_id if self.status and self.is_on else None


class PS5Coordinator(DataUpdateCoordinator[PS5Data]):
    config_entry: PS5ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PS5ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self._expected: tuple[str, float] | None = None
        self.last_status: PS5Status | None = None
        # Zuletzt wirklich gemessener Zustand (ohne Übergang), für die Ruhemodus-Wiederholung
        self.last_actual: str | None = None
        self._retry_task: asyncio.Task[None] | None = None
        # Optionaler Cloud-Teil (Spiel, Trophäen …), None = nicht eingerichtet
        self.psn: PSNCoordinator | None = None

    @property
    def host(self) -> str:
        return str(self.config_entry.data[CONF_HOST])

    async def _async_update_data(self) -> PS5Data:
        status = await async_get_status(self.host)
        if status is not None:
            self.last_status = status
        actual = STATE_OFF if status is None else STATE_ON if status.on else STATE_REST
        self.last_actual = actual
        if self._expected is not None:
            expected, until = self._expected
            if actual == expected or (expected == STATE_REST and actual == STATE_OFF):
                self._expected = None
            elif time.monotonic() > until and not self.retrying:
                # Solange die Ruhemodus-Wiederholung läuft, bleibt der Übergang; gewarnt wird erst danach
                _LOGGER.warning("PS5 hat den Befehl nicht umgesetzt (erwartet %s, ist %s)", expected, actual)
                self._expected = None
            else:
                return PS5Data(state=expected, status=status or self.last_status, transition=True)
        return PS5Data(state=actual, status=status)

    @property
    def retrying(self) -> bool:
        """Läuft gerade eine Ruhemodus-Wiederholungskette im Hintergrund?"""
        return self._retry_task is not None and not self._retry_task.done()

    def _expect(self, state: str) -> None:
        self._expected = (state, time.monotonic() + TRANSITION_SECONDS)
        current = self.data.status if self.data else self.last_status
        self.async_set_updated_data(PS5Data(state=state, status=current, transition=True))

    async def async_turn_on(self) -> None:
        if self.data and self.data.is_on:
            return
        try:
            await async_wakeup(self.host, self.config_entry.data[CONF_REGIST_KEY])
        except OSError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._expect(STATE_ON)

    async def async_turn_off(self) -> None:
        if self.data and not self.data.is_on:
            return
        try:
            await self._standby()
        except PS5AuthError as err:
            # Kopplung in der PS5 gelöscht → neu koppeln lassen
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_linked") from err
        except PS5Error as err:
            # Belegt oder Verbindung abgebrochen (z. B. kurz nach dem Aufwachen): im Hintergrund nochmal.
            # Eine noch laufende Kette wird abgebrochen, damit nie zwei parallel senden.
            _LOGGER.info("Ruhemodus nicht angenommen (%s), neuer Versuch im Hintergrund", err)
            if self.retrying and self._retry_task is not None:
                self._retry_task.cancel()
            self._retry_task = self.config_entry.async_create_background_task(
                self.hass, self._standby_retry(), f"{DOMAIN}_standby_retry"
            )
        self._expect(STATE_REST)

    async def _standby(self) -> None:
        data = self.config_entry.data
        await async_standby(self.host, data[CONF_REGIST_KEY], data[CONF_RP_KEY])

    async def _standby_retry(self) -> None:
        for delay in RETRY_DELAYS:
            await asyncio.sleep(delay)
            if self.last_actual is not None and self.last_actual != STATE_ON:
                # Inzwischen im Ruhemodus oder ganz aus (z. B. am Controller oder am Netzschalter): fertig.
                # Nur last_status reichte nicht, denn bei „ganz aus“ antwortet die PS5 gar nicht mehr.
                return
            try:
                await self._standby()
            except PS5AuthError:
                self.config_entry.async_start_reauth(self.hass)
                return
            except PS5Error as err:
                _LOGGER.debug("Ruhemodus-Wiederholung: %s", err)
                continue
            self._expect(STATE_REST)
            return
        _LOGGER.warning("PS5 hat den Ruhemodus-Befehl nach %s Versuchen nicht angenommen", len(RETRY_DELAYS) + 1)
        # Übergang beenden: ab jetzt zählt wieder der echte Zustand
        self._expected = None
        await self.async_request_refresh()
