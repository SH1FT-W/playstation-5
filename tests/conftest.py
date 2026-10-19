"""Gemeinsame Test-Bausteine: PS5-Protokoll durch Attrappen ersetzt (die echten Sockets testet test_protocol)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ps5.const import DOMAIN
from custom_components.ps5.protocol import PS5Status, Registration

HOST = "192.0.2.50"
MAC = "aa:bb:cc:dd:ee:ff"
ENTRY_DATA = {"host": HOST, "account_id": "1234567890123456789", "regist_key": "6162636431323334", "rp_key": "00" * 16}


def status(on: bool, app: tuple[str, str] | None = None) -> PS5Status:
    return PS5Status(
        on=on,
        host_id="AABBCCDDEEFF",
        host_name="PS5-123",
        host_type="PS5",
        system_version="14100003",
        app_name=app[0] if app else None,
        title_id=app[1] if app else None,
    )


@dataclass
class Console:
    """Zustand der Attrappe; die Tests setzen `answer`, die Befehle werden mitgeschrieben."""

    answer: PS5Status | None = field(default_factory=lambda: status(False))
    calls: list[str] = field(default_factory=list)
    standby_error: Exception | None = None
    standby_gate: asyncio.Event | None = None  # gesetzt: standby wartet, bis der Test das Tor öffnet
    register_error: Exception | None = None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return


@pytest.fixture
def console() -> Iterator[Console]:
    c = Console()

    async def get_status(host: str, seconds: float = 2.0) -> PS5Status | None:
        return c.answer

    async def discover(seconds: float = 2.0) -> dict[str, PS5Status]:
        return {HOST: c.answer} if c.answer else {}

    async def wakeup(host: str, regist_key: str) -> None:
        c.calls.append("wakeup")

    async def standby(host: str, regist_key: str, rp_key: str, seconds: float = 5.0) -> None:
        c.calls.append("standby")
        if c.standby_gate is not None:
            await c.standby_gate.wait()
        if c.standby_error:
            raise c.standby_error

    async def register(host: str, account_id: str, pin: str, seconds: float = 5.0) -> Registration:
        c.calls.append(f"register:{pin}")
        if c.register_error:
            raise c.register_error
        return Registration(regist_key="6162636431323334", rp_key="00" * 16, nickname="PS5-123", mac="AABBCCDDEEFF")

    with (
        patch("custom_components.ps5.coordinator.async_get_status", get_status),
        patch("custom_components.ps5.coordinator.async_wakeup", wakeup),
        patch("custom_components.ps5.coordinator.async_standby", standby),
        patch("custom_components.ps5.config_flow.async_get_status", get_status),
        patch("custom_components.ps5.config_flow.async_discover", discover),
        patch("custom_components.ps5.config_flow.async_register", register),
    ):
        yield c


@pytest.fixture
async def setup(hass: HomeAssistant, console: Console) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="PS5-123", unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
