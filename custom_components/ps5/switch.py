"""Ein/Aus als Schalter: an = PS5 läuft, aus = Ruhemodus (für Automationen wie HDMI-Umschalten)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PS5ConfigEntry, PS5Coordinator
from .entity import PS5Entity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: PS5ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([PS5PowerSwitch(entry.runtime_data)])


class PS5PowerSwitch(PS5Entity, SwitchEntity):
    def __init__(self, coordinator: PS5Coordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off()
