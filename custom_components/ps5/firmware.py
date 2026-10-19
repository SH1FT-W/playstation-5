"""Neueste PS5-Systemsoftware von Sonys öffentlicher Update-Seite (kein Konto nötig), alle 6 Stunden.

Die Seite listet „Version: 26.06-14.10.00“ mit Hinweisen; die PS5 meldet lokal 14100003 → beides wird auf
„14.10.00“ gebracht. Installieren kann man von außen nicht – das macht die PS5 selbst (im Ruhemodus, wenn
in ihren Einstellungen eingeschaltet).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import html
import logging
import re

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

INFO_URL = "https://www.playstation.com/{locale}/support/hardware/ps5/system-software-info/"
VERSION_RE = re.compile(r"(\d{2}\.\d{2})-(\d{2}\.\d{2}\.\d{2})")


@dataclass(frozen=True, slots=True)
class Firmware:
    version: str  # 14.10.00 – vergleichbar mit der PS5
    label: str  # 26.06-14.10.00 – wie Sony sie nennt
    notes: str
    url: str


def parse_info_page(page: str, url: str) -> Firmware | None:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    matches = list(VERSION_RE.finditer(text))
    if not matches:
        return None
    first = matches[0]
    end = matches[1].start() if len(matches) > 1 else len(text)
    notes = text[first.end() : end]
    # „Frühere Systemsoftware-Updates …“ / „Previous system software updates …“ gehört nicht mehr dazu
    notes = re.split(r"Frühere|Previous|Version:", notes)[0].strip()
    return Firmware(version=first.group(2), label=first.group(0), notes=notes, url=url)


def local_version(firmware: str | None) -> str | None:
    """14.10.00.03 (aus system-version) → 14.10.00."""
    if not firmware:
        return None
    parts = firmware.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else firmware


class FirmwareCoordinator(DataUpdateCoordinator[Firmware | None]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_firmware", update_interval=timedelta(hours=6)
        )
        locale = "de-de" if hass.config.language.startswith("de") else "en-us"
        self.url = INFO_URL.format(locale=locale)

    async def _async_update_data(self) -> Firmware | None:
        try:
            async with asyncio.timeout(30):
                resp = await async_get_clientsession(self.hass).get(self.url, headers={"User-Agent": "Mozilla/5.0"})
                async with resp:
                    if resp.status != 200:
                        raise UpdateFailed(
                            translation_domain=DOMAIN,
                            translation_key="firmware_unreachable",
                            translation_placeholders={"error": f"HTTP {resp.status}"},
                        )
                    page = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="firmware_unreachable",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err
        firmware = parse_info_page(page, self.url)
        if firmware is None:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="firmware_no_version")
        return firmware
