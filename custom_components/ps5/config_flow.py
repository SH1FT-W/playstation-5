"""Einrichtung: PS5 finden (Broadcast oder DHCP), PSN-Konto-ID, PIN aus „Gerät verknüpfen“."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
import voluptuous as vol

from .cloud import SSO_URL, async_activate, deactivate
from .const import (
    CONF_ACCOUNT_ID,
    CONF_DISABLE_CLOUD,
    CONF_NPSSO,
    CONF_PIN,
    CONF_PSN_TOKEN,
    CONF_REGIST_KEY,
    CONF_RP_KEY,
    DOMAIN,
)
from .protocol import (
    PS5AuthError,
    PS5Error,
    PS5Status,
    account_id_b64,
    async_discover,
    async_get_status,
    async_register,
)
from .psn import PSNError, PSNTokenError, async_account_id

_PIN = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.NUMBER))
_SECRET = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


def _valid_pin(pin: str) -> bool:
    return pin.isdigit() and len(pin) == 8


class PS5OptionsFlow(OptionsFlow):
    """PSN-Cloud (Spiel, Trophäen …) einschalten, Token erneuern oder ausschalten."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        active = bool(entry.data.get(CONF_PSN_TOKEN))
        errors: dict[str, str] = {}
        if user_input is not None:
            npsso = str(user_input.get(CONF_NPSSO) or "").strip()
            if user_input.get(CONF_DISABLE_CLOUD):
                deactivate(self.hass, entry)
                return self.async_create_entry(data={})
            if not npsso:
                return self.async_create_entry(data={})  # nichts geändert
            try:
                await async_activate(self.hass, entry, npsso)
            except PSNTokenError:
                errors[CONF_NPSSO] = "invalid_npsso"
            except PSNError:
                errors["base"] = "psn_unreachable"
            else:
                return self.async_create_entry(data={})
        fields: dict[vol.Marker, Any] = {vol.Optional(CONF_NPSSO): _SECRET}
        if active:
            fields[vol.Optional(CONF_DISABLE_CLOUD, default=False)] = bool
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
            description_placeholders={"sso_url": SSO_URL},
            errors=errors,
        )


class PS5ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PS5OptionsFlow:
        return PS5OptionsFlow()

    def __init__(self) -> None:
        self._host = ""
        self._status: PS5Status | None = None
        self._found: dict[str, PS5Status] = {}
        self._account = ""

    def _psn_account_id(self) -> str:
        """Konto-ID aus der PlayStation-Network-Integration vorschlagen (deren unique_id), falls vorhanden."""
        for entry in self.hass.config_entries.async_entries("playstation_network"):
            if entry.unique_id and entry.unique_id.isdigit():
                return entry.unique_id
        return ""

    async def _use_host(self, host: str) -> str | None:
        status = await async_get_status(host)
        if status is None:
            return "cannot_connect"
        await self.async_set_unique_id(format_mac(status.mac))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._host, self._status = host, status
        self.context["title_placeholders"] = {"name": status.host_name}
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._use_host(user_input[CONF_HOST])) is None:
                return await self.async_step_link()
            errors["base"] = error
        else:
            self._found = await async_discover()
        hosts = list(self._found)
        default = hosts[0] if hosts else vol.UNDEFINED
        field: Any = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=ip, label=f"{status.host_name} ({ip})")
                        for ip, status in self._found.items()
                    ],
                    custom_value=True,
                )
            )
            if hosts
            else str
        )
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required(CONF_HOST, default=default): field}), errors=errors
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        await self.async_set_unique_id(format_mac(discovery_info.macaddress))
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})
        if await self._use_host(discovery_info.ip) is not None:
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_link()

    async def async_step_link(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Konto-ID (oder NPSSO zum Nachschlagen) + PIN; die PS5 muss gerade „Gerät verknüpfen“ zeigen."""
        errors: dict[str, str] = {}
        if user_input is not None:
            account = str(user_input.get(CONF_ACCOUNT_ID) or "").strip()
            npsso = str(user_input.get(CONF_NPSSO) or "").strip()
            pin = str(user_input[CONF_PIN]).strip()
            if not account and npsso:
                try:
                    account = await async_account_id(async_get_clientsession(self.hass), npsso)
                except PSNTokenError:
                    errors[CONF_NPSSO] = "invalid_npsso"
                except PSNError:
                    errors["base"] = "psn_unreachable"
                else:
                    self._account = account  # bleibt im Formular stehen, falls die PIN noch nicht passt
            elif not account:
                errors["base"] = "account_missing"
            if account and not errors:
                try:
                    account_id_b64(account)
                except ValueError:
                    errors[CONF_ACCOUNT_ID] = "invalid_account"
            if not _valid_pin(pin):
                errors[CONF_PIN] = "invalid_pin"
            if not errors:
                try:
                    reg = await async_register(self._host, account, pin)
                except PS5AuthError:
                    errors["base"] = "link_failed"
                except PS5Error:
                    errors["base"] = "not_linking"
                else:
                    data = {
                        CONF_HOST: self._host,
                        CONF_ACCOUNT_ID: account,
                        CONF_REGIST_KEY: reg.regist_key,
                        CONF_RP_KEY: reg.rp_key,
                    }
                    if self.source == "reauth":
                        # data_updates statt data: die PSN-Cloud-Felder im Eintrag bleiben erhalten
                        return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
                    title = self._status.host_name if self._status else reg.nickname
                    return self.async_create_entry(title=title, data=data)
        account_default = self._account or self._psn_account_id()
        return self.async_show_form(
            step_id="link",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ACCOUNT_ID, default=account_default): str,
                    vol.Optional(CONF_NPSSO): _SECRET,
                    vol.Required(CONF_PIN): _PIN,
                }
            ),
            description_placeholders={
                "name": self._status.host_name if self._status else "PS5",
                "sso_url": SSO_URL,
            },
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Kopplung wurde in der PS5 gelöscht: neu koppeln."""
        self._host = entry_data[CONF_HOST]
        self._account = str(entry_data.get(CONF_ACCOUNT_ID, ""))
        self._status = await async_get_status(self._host)
        return await self.async_step_link()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Neue IP-Adresse (die Kopplung bleibt)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            status = await async_get_status(user_input[CONF_HOST])
            if status is None:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(format_mac(status.mac))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data={**entry.data, CONF_HOST: user_input[CONF_HOST]})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )
