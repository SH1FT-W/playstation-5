"""PS Plus abonniert (aus der PSN-Cloud, nur wenn eingerichtet)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PS5ConfigEntry
from .entity import PSNEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PS5ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.psn:
        async_add_entities([PSPlusSensor(coordinator, coordinator.psn, "ps_plus")])


class PSPlusSensor(PSNEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.profile.get("isPlus")
        return bool(value) if value is not None else None
