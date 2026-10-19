"""Systemsoftware: installiert (lokal von der PS5) gegen neueste (Sonys Update-Seite). Nur Anzeige."""

from __future__ import annotations

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import PS5ConfigEntry, PS5Coordinator
from .entity import PS5Entity
from .firmware import FirmwareCoordinator, local_version

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PS5ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    firmware = FirmwareCoordinator(hass, entry)
    # Sonys Update-Seite im Hintergrund holen: das Setup wartet nicht darauf
    entry.async_create_background_task(hass, firmware.async_refresh(), f"{DOMAIN}_firmware_first_refresh")
    async_add_entities([PS5FirmwareUpdate(entry.runtime_data, firmware)])


class PS5FirmwareUpdate(PS5Entity, UpdateEntity):
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # Kein INSTALL: die PS5 lässt sich das Update nicht von außen anstoßen (macht es selbst im Ruhemodus)

    def __init__(self, coordinator: PS5Coordinator, firmware: FirmwareCoordinator) -> None:
        super().__init__(coordinator, "firmware")
        self._firmware = firmware

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._firmware.async_add_listener(self._firmware_updated))

    @callback
    def _firmware_updated(self) -> None:
        self.async_write_ha_state()

    @property
    def installed_version(self) -> str | None:
        status = self.coordinator.data.status or self.coordinator.last_status
        return local_version(status.firmware if status else None)

    @property
    def latest_version(self) -> str | None:
        latest = self._firmware.data
        return latest.version if latest else self.installed_version

    @property
    def release_summary(self) -> str | None:
        latest = self._firmware.data
        return f"{latest.label}: {latest.notes}"[:255] if latest else None

    @property
    def release_url(self) -> str | None:
        return self._firmware.url
