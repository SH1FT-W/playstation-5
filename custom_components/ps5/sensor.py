"""Status (an / Ruhemodus / aus) und laufendes Spiel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .cloud import PSNCoordinator, PSNData
from .coordinator import STATE_OFF, STATE_ON, STATE_REST, PS5ConfigEntry, PS5Coordinator
from .entity import PS5Entity, PSNEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PS5ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [PS5StatusSensor(coordinator), PS5GameSensor(coordinator)]
    if coordinator.psn:
        entities += [PSNSensor(coordinator, coordinator.psn, d) for d in PSN_SENSORS]
        entities.append(PSNTokenExpirySensor(coordinator, coordinator.psn, "psn_token_expires"))
    async_add_entities(entities)


class PS5StatusSensor(PS5Entity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: PS5Coordinator) -> None:
        super().__init__(coordinator, "status")
        self._attr_options = [STATE_ON, STATE_REST, STATE_OFF]

    @property
    def native_value(self) -> str:
        return self.coordinator.data.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"changing": self.coordinator.data.transition}


def cloud_game(coordinator: PS5Coordinator) -> dict[str, Any]:
    """Laufendes Spiel aus der PSN-Cloud, aber nur solange die PS5 lokal an ist (die PS5 meldet es selbst nicht)."""
    psn = coordinator.psn
    if not coordinator.data.is_on or psn is None or not psn.last_update_success or psn.data is None:
        return {}
    return psn.data.game if psn.data.playing_on_ps5 else {}


class PS5GameSensor(PS5Entity, SensorEntity):
    """Laufendes Spiel: lokal (falls die Konsole es meldet) oder aus der PSN-Cloud."""

    def __init__(self, coordinator: PS5Coordinator) -> None:
        super().__init__(coordinator, "game")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.psn:
            self.async_on_remove(self.coordinator.psn.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.app_name or cloud_game(self.coordinator).get("titleName")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        game = cloud_game(self.coordinator)
        return {
            "title_id": self.coordinator.data.title_id or game.get("npTitleId"),
            "format": game.get("format"),
            "cover": game.get("conceptIconUrl") or game.get("npTitleIconUrl"),
        }


ONLINE_STATES = ["offline", "availabletoplay", "availabletocommunicate", "busy"]


@dataclass(frozen=True, kw_only=True)
class PSNSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PSNData], StateType | datetime]


def _trophy(key: str) -> Callable[[PSNData], StateType | datetime]:
    return lambda d: (d.trophies.get("earnedTrophies") or {}).get(key)


def _online(d: PSNData) -> str | None:
    value = str(d.presence.get("availability") or "").lower().replace("unavailable", "offline")
    return value if value in ONLINE_STATES else None


def _expires(coordinator: PSNCoordinator) -> datetime | None:
    expires = coordinator.client.refresh_expires_at
    return dt_util.utc_from_timestamp(expires) if expires else None


PSN_SENSORS: tuple[PSNSensorDescription, ...] = (
    PSNSensorDescription(
        key="online_status", device_class=SensorDeviceClass.ENUM, options=ONLINE_STATES, value_fn=_online
    ),
    PSNSensorDescription(
        key="last_online",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: (
            dt_util.parse_datetime(str(d.presence["lastAvailableDate"]))
            if d.presence.get("lastAvailableDate")
            else None
        ),
    ),
    PSNSensorDescription(
        key="trophy_level", state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: d.trophies.get("trophyLevel")
    ),
    PSNSensorDescription(
        key="trophy_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.trophies.get("progress"),
    ),
    PSNSensorDescription(key="trophies_platinum", state_class=SensorStateClass.TOTAL, value_fn=_trophy("platinum")),
    PSNSensorDescription(key="trophies_gold", state_class=SensorStateClass.TOTAL, value_fn=_trophy("gold")),
    PSNSensorDescription(key="trophies_silver", state_class=SensorStateClass.TOTAL, value_fn=_trophy("silver")),
    PSNSensorDescription(key="trophies_bronze", state_class=SensorStateClass.TOTAL, value_fn=_trophy("bronze")),
    PSNSensorDescription(
        key="online_id", entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda d: d.profile.get("onlineId")
    ),
)


class PSNSensor(PSNEntity, SensorEntity):
    entity_description: PSNSensorDescription

    def __init__(self, coordinator: PS5Coordinator, psn: PSNCoordinator, description: PSNSensorDescription) -> None:
        super().__init__(coordinator, psn, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)


class PSNTokenExpirySensor(PSNEntity, SensorEntity):
    """Wann das gespeicherte PSN-Erneuerungs-Token abläuft (dann neues NPSSO über die Reparatur)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True  # gerade bei abgelaufenem Token soll man das Datum sehen

    @property
    def native_value(self) -> datetime | None:
        return _expires(self.coordinator)
