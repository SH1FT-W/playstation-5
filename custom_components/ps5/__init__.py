"""PlayStation 5: lokal über DDP (Status, Aufwecken) und Remote Play (Kopplung, Ruhemodus) – ohne Cloud."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .cloud import PSNCoordinator
from .const import CONF_PSN_TOKEN, DOMAIN
from .coordinator import PS5ConfigEntry, PS5Coordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.IMAGE,
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: PS5ConfigEntry) -> bool:
    coordinator = PS5Coordinator(hass, entry)
    # Kein Fehler, wenn die PS5 aus ist: dann ist sie eben „aus“
    await coordinator.async_config_entry_first_refresh()
    if entry.data.get(CONF_PSN_TOKEN):
        coordinator.psn = PSNCoordinator(hass, entry)
        # Erste Cloud-Abfrage im Hintergrund: das Setup wartet nicht auf Sony (bis 30 s). Cloud weg oder
        # Token abgelaufen: nur die Cloud-Entitäten sind dann nicht verfügbar, der lokale Teil läuft weiter.
        entry.async_create_background_task(hass, coordinator.psn.async_refresh(), f"{DOMAIN}_psn_first_refresh")
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PS5ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
