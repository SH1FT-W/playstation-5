"""Diagnose-Download ohne Kopplungsschlüssel, Konto-ID, Adresse und MAC."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_ACCOUNT_ID, CONF_PSN_ACCOUNT_ID, CONF_PSN_TOKEN, CONF_REGIST_KEY, CONF_RP_KEY
from .coordinator import PS5ConfigEntry

TO_REDACT = {CONF_HOST, CONF_ACCOUNT_ID, CONF_PSN_ACCOUNT_ID, CONF_PSN_TOKEN, CONF_REGIST_KEY, CONF_RP_KEY, "host_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: PS5ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "state": data.state if data else None,
        "transition": data.transition if data else None,
        "status": async_redact_data(asdict(data.status), TO_REDACT) if data and data.status else None,
    }
