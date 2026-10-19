"""Einrichtung (Suche, PIN), DHCP-Erkennung, Neu koppeln, Neu konfigurieren."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ps5 import psn
from custom_components.ps5.const import DOMAIN
from custom_components.ps5.protocol import PS5AuthError, PS5Error

from .conftest import ENTRY_DATA, HOST, MAC, Console

LINK = {"account_id": "1234567890123456789", "pin": "12345678"}


async def test_user_flow(hass: HomeAssistant, console: Console) -> None:
    # PSN-Integration vorhanden → Konto-ID vorausgefüllt
    MockConfigEntry(domain="playstation_network", unique_id="1234567890123456789").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["step_id"] == "link"
    schema = result["data_schema"].schema
    default = next(k.default() for k in schema if k == "account_id")
    assert default == "1234567890123456789"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**LINK, "pin": "123"})
    assert result["errors"] == {"pin": "invalid_pin"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**LINK, "account_id": "abc"})
    assert result["errors"] == {"account_id": "invalid_account"}
    console.register_error = PS5Error("nicht im Kopplungsmodus")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LINK)
    assert result["errors"] == {"base": "not_linking"}
    console.register_error = PS5AuthError("falsche PIN")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LINK)
    assert result["errors"] == {"base": "link_failed"}
    console.register_error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LINK)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PS5-123"
    assert result["result"].unique_id == MAC
    assert result["data"]["regist_key"] == "6162636431323334"


async def test_user_flow_no_answer(hass: HomeAssistant, console: Console) -> None:
    console.answer = None
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.99"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_dhcp(hass: HomeAssistant, console: Console) -> None:
    info = DhcpServiceInfo(ip=HOST, hostname="ps5-123", macaddress="aabbccddeeff")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=info)
    assert result["step_id"] == "link"
    hass.config_entries.flow.async_abort(result["flow_id"])

    # schon eingerichtet → nur die Adresse nachziehen
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={**ENTRY_DATA, "host": "192.0.2.1"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=info)
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == HOST


async def test_dhcp_no_answer(hass: HomeAssistant, console: Console) -> None:
    console.answer = None
    info = DhcpServiceInfo(ip=HOST, hostname="ps5-123", macaddress="aabbccddeeff")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=info)
    assert result["reason"] == "cannot_connect"


async def test_reauth(hass: HomeAssistant, setup: MockConfigEntry, console: Console) -> None:
    # Cloud eingerichtet: Neu koppeln darf die PSN-Felder nicht wegwerfen
    hass.config_entries.async_update_entry(
        setup, data={**setup.data, "psn_account_id": "1234567890123456789", "psn_refresh_token": "refresh-1"}
    )
    result = await setup.start_reauth_flow(hass)
    assert result["step_id"] == "link"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LINK)
    assert result["reason"] == "reauth_successful"
    assert console.calls[-1] == "register:12345678"
    assert setup.data["regist_key"] == "6162636431323334"
    assert setup.data["psn_refresh_token"] == "refresh-1"
    assert setup.data["psn_account_id"] == "1234567890123456789"
    await hass.async_block_till_done()


async def test_reconfigure(hass: HomeAssistant, setup: MockConfigEntry, console: Console) -> None:
    result = await setup.start_reconfigure_flow(hass)
    console.answer = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.77"})
    assert result["errors"] == {"base": "cannot_connect"}
    from .conftest import status

    console.answer = status(False)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.0.2.77"})
    assert result["reason"] == "reconfigure_successful"
    assert setup.data["host"] == "192.0.2.77"
    await hass.async_block_till_done()


def test_translations_complete() -> None:
    root = Path(__file__).parent.parent / "custom_components" / "ps5"

    def keys(tree: object, prefix: str = "") -> set[str]:
        if not isinstance(tree, dict):
            return {prefix}
        return {k for key, value in tree.items() for k in keys(value, f"{prefix}.{key}")}

    strings = keys(json.loads((root / "strings.json").read_text()))
    for language in ("en", "de"):
        assert keys(json.loads((root / "translations" / f"{language}.json").read_text())) == strings


async def _to_link(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["step_id"] == "link"
    return str(result["flow_id"])


def _mock_sony(aioclient_mock: AiohttpClientMocker, *, code: bool = True, account_status: int = 200) -> None:
    location = (
        "com.scee.psxandroid.scecompcall://redirect?code=v3.ABC"
        if code
        else "x://redirect?error=login_required&error_code=4165"
    )
    aioclient_mock.get(psn.AUTHORIZE_URL, status=302, headers={"Location": location})
    aioclient_mock.post(psn.TOKEN_URL, json={"access_token": "tok"})
    aioclient_mock.get(psn.ACCOUNT_URL, status=account_status, json={"accountId": "1234567890123456789"})


async def test_link_with_npsso(hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker) -> None:
    _mock_sony(aioclient_mock)
    flow_id = await _to_link(hass)
    # das ganze kopierte JSON geht auch
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"account_id": "", "npsso": '{"npsso":"' + "a" * 64 + '"}', "pin": "12345678"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["account_id"] == "1234567890123456789"
    assert "npsso" not in result["data"]  # nicht gespeichert
    _method, _url, _data, headers = aioclient_mock.mock_calls[0]
    assert headers["Cookie"] == "npsso=" + "a" * 64


async def test_link_npsso_errors(hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker) -> None:
    flow_id = await _to_link(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {"account_id": "", "pin": "12345678"})
    assert result["errors"] == {"base": "account_missing"}

    _mock_sony(aioclient_mock, code=False)
    result = await hass.config_entries.flow.async_configure(flow_id, {"npsso": "alt", "pin": "12345678"})
    assert result["errors"] == {"npsso": "invalid_npsso"}

    aioclient_mock.clear_requests()
    _mock_sony(aioclient_mock, account_status=500)
    result = await hass.config_entries.flow.async_configure(flow_id, {"npsso": "x", "pin": "12345678"})
    assert result["errors"] == {"base": "psn_unreachable"}

    # Kopplung scheitert an der PIN → die nachgeschlagene ID bleibt im Formular
    aioclient_mock.clear_requests()
    _mock_sony(aioclient_mock)
    console.register_error = PS5AuthError("falsche PIN")
    result = await hass.config_entries.flow.async_configure(flow_id, {"npsso": "x", "pin": "12345678"})
    assert result["errors"] == {"base": "link_failed"}
    default = next(k.default() for k in result["data_schema"].schema if k == "account_id")
    assert default == "1234567890123456789"
