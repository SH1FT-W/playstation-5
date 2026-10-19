"""Reparatur „PSN-Token läuft ab / ist abgelaufen“: neues NPSSO einfügen, fertig."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import voluptuous as vol

from .cloud import SSO_URL, async_activate
from .const import CONF_NPSSO
from .psn import PSNError, PSNTokenError

_SECRET = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


class PSNTokenRepair(RepairsFlow):
    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_missing")
        if user_input is not None:
            try:
                await async_activate(self.hass, entry, str(user_input[CONF_NPSSO]))
            except PSNTokenError:
                errors[CONF_NPSSO] = "invalid_npsso"
            except PSNError:
                errors["base"] = "psn_unreachable"
            else:
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(CONF_NPSSO): _SECRET}),
            description_placeholders={"name": entry.title, "sso_url": SSO_URL},
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    return PSNTokenRepair(str((data or {})["entry_id"]))
