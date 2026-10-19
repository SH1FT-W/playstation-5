"""Gemeinsame Basis: ein Gerät je PS5 (über die MAC)."""

from __future__ import annotations

import re

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud import PSNCoordinator
from .const import DOMAIN
from .coordinator import PS5Coordinator

_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def ps5_device_info(coordinator: PS5Coordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    mac = entry.unique_id or entry.entry_id
    status = coordinator.last_status
    info = DeviceInfo(
        identifiers={(DOMAIN, mac)},
        manufacturer="Sony Interactive Entertainment",
        model="PlayStation 5",
        name=entry.title,
        sw_version=status.firmware if status else None,
    )
    if _MAC.match(mac):
        # Nur eine echte MAC als Netzwerkverbindung melden (sonst stünde die entry_id dort)
        info["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac)}
    return info


class PS5Entity(CoordinatorEntity[PS5Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PS5Coordinator, key: str | None) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        mac = entry.unique_id or entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{mac}_{key}" if key else mac
        self._attr_device_info = ps5_device_info(coordinator)


class PSNEntity(CoordinatorEntity[PSNCoordinator]):
    """Wert aus der PSN-Cloud, am selben Gerät wie die lokalen Entitäten."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PS5Coordinator, psn: PSNCoordinator, key: str) -> None:
        super().__init__(psn)
        mac = coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{mac}_psn_{key}"
        self._attr_device_info = ps5_device_info(coordinator)

    @property
    def available(self) -> bool:
        # Die erste Cloud-Abfrage läuft im Hintergrund: bis dahin gibt es noch keine Daten
        return super().available and self.coordinator.data is not None
