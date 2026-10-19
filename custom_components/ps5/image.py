"""Spiel-Cover und Avatar (Bild-URLs aus der PSN-Cloud, nur wenn eingerichtet)."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .cloud import PSNCoordinator, PSNData
from .coordinator import PS5ConfigEntry, PS5Coordinator
from .entity import PSNEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PS5ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.psn:
        psn = coordinator.psn
        async_add_entities(
            [
                PSNImage(hass, coordinator, psn, "game_cover", lambda d: d.cover_url if d.playing_on_ps5 else None),
                PSNImage(hass, coordinator, psn, "avatar", lambda d: d.avatar_url),
            ]
        )


class PSNImage(PSNEntity, ImageEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: PS5Coordinator,
        psn: PSNCoordinator,
        key: str,
        url_fn: Callable[[PSNData], str | None],
    ) -> None:
        PSNEntity.__init__(self, coordinator, psn, key)
        ImageEntity.__init__(self, hass)
        self._url_fn = url_fn
        self._attr_image_url = url_fn(psn.data) if psn.data else None
        if self._attr_image_url:
            # sonst steht das Bild bis zur ersten Änderung auf „unbekannt“
            self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        url = self._url_fn(self.coordinator.data) if self.coordinator.data else None
        if url != self._attr_image_url:
            self._attr_image_url = url
            self._attr_image_last_updated = dt_util.utcnow()
            self._cached_image = None
        super()._handle_coordinator_update()
