"""PSN-Cloud: Spiel/Trophäen/Profil, Token-Erneuerung, Reparatur bei Ablauf, Optionen; Firmware-Update."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.ps5 import psn
from custom_components.ps5.cloud import PSNCoordinator, issue_id
from custom_components.ps5.const import DOMAIN
from custom_components.ps5.firmware import INFO_URL, FirmwareCoordinator, local_version, parse_info_page

from .conftest import ENTRY_DATA, MAC, Console, status

ACCOUNT = "1234567890123456789"
CLOUD_DATA = {**ENTRY_DATA, "psn_account_id": ACCOUNT, "psn_refresh_token": "refresh-1", "psn_refresh_expires": None}

PRESENCE = {
    "basicPresence": {
        "availability": "availableToPlay",
        "lastAvailableDate": "2026-10-13T18:00:00.000Z",
        "primaryPlatformInfo": {"onlineStatus": "online", "platform": "PS5"},
        "gameTitleInfoList": [
            {
                "npTitleId": "PPSA28000_00",
                "titleName": "EA SPORTS FC 26",
                "format": "PS5",
                "conceptIconUrl": "https://image.api.playstation.com/fc26.png",
            }
        ],
    }
}
TROPHIES = {
    "trophyLevel": 312,
    "progress": 45,
    "earnedTrophies": {"platinum": 7, "gold": 80, "silver": 200, "bronze": 900},
}
PROFILE = {
    "onlineId": "Testspieler_42",
    "isPlus": True,
    "avatars": [{"size": "s", "url": "s.png"}, {"size": "xl", "url": "xl.png"}],
}
INFO_PAGE = (
    "<html><h2>Neueste Updates</h2><p>Version: 26.06-14.20.00</p><p>Wir haben die Stabilität verbessert.</p>"
    "<h3>Frühere Systemsoftware-Updates</h3><p>Version: 26.06-14.10.00</p><p>Alt.</p></html>"
)


def mock_psn(
    mock: AiohttpClientMocker,
    *,
    token_status: int = 200,
    presence: dict[str, Any] = PRESENCE,
    refresh_expires_in: int | None = 5_184_000,
) -> None:
    mock.clear_requests()
    token = {"access_token": "acc", "expires_in": 3600, "refresh_token": "refresh-2"}
    if refresh_expires_in is not None:
        token["refresh_token_expires_in"] = refresh_expires_in
    mock.post(psn.TOKEN_URL, status=token_status, json=token)
    mock.get(psn.PRESENCE_URL.format(account_id=ACCOUNT), json=presence)
    mock.get(psn.TROPHY_URL.format(account_id=ACCOUNT), json=TROPHIES)
    mock.get(psn.PROFILE_URL.format(account_id=ACCOUNT), json=PROFILE)
    mock.get(INFO_URL.format(locale="en-us"), text=INFO_PAGE)
    mock.get("https://image.api.playstation.com/fc26.png", content=b"png")


async def _setup(hass: HomeAssistant, data: dict[str, Any] = CLOUD_DATA) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="PS5-123", unique_id=MAC, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    # Cloud und Sony-Update-Seite werden im Hintergrund geholt
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_setup_does_not_wait_for_cloud(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    """Das Setup ist fertig, bevor Sony geantwortet hat; die Cloud-Entitäten sind bis dahin nicht verfügbar."""
    mock_psn(aioclient_mock)
    gate = asyncio.Event()  # Sony antwortet erst, wenn der Test das Tor öffnet
    original_psn, original_fw = PSNCoordinator._async_update_data, FirmwareCoordinator._async_update_data

    async def slow_psn(self: PSNCoordinator) -> Any:
        await gate.wait()
        return await original_psn(self)

    async def slow_fw(self: FirmwareCoordinator) -> Any:
        await gate.wait()
        return await original_fw(self)

    with (
        patch.object(PSNCoordinator, "_async_update_data", slow_psn),
        patch.object(FirmwareCoordinator, "_async_update_data", slow_fw),
    ):
        entry = MockConfigEntry(domain=DOMAIN, title="PS5-123", unique_id=MAC, data=CLOUD_DATA)
        entry.add_to_hass(hass)
        async with asyncio.timeout(2):
            assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("switch.ps5_123_power").state == "off"  # lokal sofort da
        trophies = hass.states.get("sensor.ps5_123_trophy_level")
        assert trophies is not None and trophies.state == "unavailable"
        assert hass.states.get("binary_sensor.ps5_123_playstation_plus").state == "unavailable"
        assert hass.states.get("image.ps5_123_avatar").state == "unavailable"
        assert hass.states.get("update.ps5_123_system_software").state == "off"  # neueste Version noch unbekannt
        gate.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.ps5_123_trophy_level").state == "312"
    assert hass.states.get("binary_sensor.ps5_123_playstation_plus").state == "on"
    assert hass.states.get("update.ps5_123_system_software").state == "on"


async def test_cloud_values(hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker) -> None:
    console.answer = status(True)  # PS5 an, meldet lokal aber kein Spiel
    mock_psn(aioclient_mock)
    entry = await _setup(hass)
    state = hass.states.get
    assert state("sensor.ps5_123_game").state == "EA SPORTS FC 26"
    assert state("sensor.ps5_123_game").attributes["title_id"] == "PPSA28000_00"
    mp = state("media_player.ps5_123")
    assert mp.state == "playing" and mp.attributes["media_title"] == "EA SPORTS FC 26"
    assert mp.attributes["entity_picture"]
    assert state("sensor.ps5_123_online_status").state == "availabletoplay"
    assert state("sensor.ps5_123_trophy_level").state == "312"
    assert state("sensor.ps5_123_platinum_trophies").state == "7"
    assert state("binary_sensor.ps5_123_playstation_plus").state == "on"
    assert state("sensor.ps5_123_online_id").state == "Testspieler_42"
    assert state("sensor.ps5_123_last_online").state.startswith("2026-10-13T18:00")
    assert state("image.ps5_123_game_cover").state not in ("unavailable", "unknown")
    assert state("image.ps5_123_avatar").state not in ("unavailable", "unknown")
    # Erneuertes Token wurde im Eintrag abgelegt, Ablauf als Sensor sichtbar
    assert entry.data["psn_refresh_token"] == "refresh-2"
    assert entry.data["psn_refresh_expires"] > time.time() + 50 * 86400
    assert state("sensor.ps5_123_psn_login_expires").state != "unknown"


async def test_game_only_while_ps5_on(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_psn(aioclient_mock)
    await _setup(hass)  # PS5 im Ruhemodus → kein Spiel, auch wenn PSN noch eins meldet
    assert hass.states.get("sensor.ps5_123_game").state == "unknown"
    assert hass.states.get("media_player.ps5_123").state == "off"


async def test_token_expired_creates_repair(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker, hass_client: ClientSessionGenerator
) -> None:
    mock_psn(aioclient_mock, token_status=400)
    entry = await _setup(hass)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id))
    assert issue is not None and issue.is_fixable
    assert hass.states.get("sensor.ps5_123_trophy_level").state == "unavailable"
    assert hass.states.get("switch.ps5_123_power").state == "off"  # lokal läuft weiter

    # Reparatur: neues NPSSO einfügen
    assert await async_setup_component(hass, "repairs", {})
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    aioclient_mock.get(psn.AUTHORIZE_URL, status=302, headers={"Location": "x://r?code=c"})
    aioclient_mock.post(
        psn.TOKEN_URL, json={"access_token": "a", "refresh_token": "neu", "refresh_token_expires_in": 100}
    )
    aioclient_mock.get(psn.ACCOUNT_URL, json={"accountId": ACCOUNT})
    client = await hass_client()
    resp = await client.post("/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id(entry.entry_id)})
    flow = await resp.json()
    assert flow["step_id"] == "confirm"
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={"npsso": "frisch"})
    assert (await resp.json())["type"] == "create_entry"
    assert entry.data["psn_refresh_token"] == "neu"
    assert entry.data["psn_npsso"] == "frisch"  # bleibt für die automatische Erneuerung
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id)) is None
    await hass.async_block_till_done()


async def test_expiring_soon_warns(hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker) -> None:
    mock_psn(aioclient_mock, refresh_expires_in=3 * 86400)
    entry = await _setup(hass)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id))
    assert issue is not None and issue.translation_key == "psn_token_expiring"


NPSSO_DATA = {**CLOUD_DATA, "psn_npsso": "gespeichert"}


def _authorize_calls(mock: AiohttpClientMocker) -> int:
    return sum(1 for call in mock.mock_calls if str(call[1]).startswith(psn.AUTHORIZE_URL))


async def test_no_early_warning_with_npsso(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sony verlängert das Erneuerungs-Token nicht (~10 Tage fest): mit NPSSO keine Reparatur nach 3 Tagen."""
    mock_psn(aioclient_mock, refresh_expires_in=5 * 86400)
    entry = await _setup(hass, NPSSO_DATA)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id)) is None
    assert _authorize_calls(aioclient_mock) == 0  # noch nicht fällig


async def test_renews_via_npsso_before_expiry(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_psn(aioclient_mock)
    aioclient_mock.get(psn.AUTHORIZE_URL, status=302, headers={"Location": "x://r?code=c"})
    entry = await _setup(hass, {**NPSSO_DATA, "psn_refresh_expires": time.time() + 86400})
    assert _authorize_calls(aioclient_mock) == 1
    assert entry.data["psn_refresh_token"] == "refresh-2"
    assert entry.data["psn_refresh_expires"] > time.time() + 50 * 86400
    assert entry.data["psn_npsso"] == "gespeichert"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id)) is None
    assert hass.states.get("sensor.ps5_123_trophy_level").state == "312"


async def test_expired_token_rescued_by_npsso(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    """Erneuerungs-Token schon abgelaufen (HA lange aus): das NPSSO holt ein neues, keine Reparatur."""
    mock_psn(aioclient_mock, token_status=400)
    fresh = {"access_token": "acc", "expires_in": 3600, "refresh_token": "aus-npsso", "refresh_token_expires_in": 9e5}
    with patch("custom_components.ps5.cloud.async_token_from_npsso", return_value=fresh) as renew:
        entry = await _setup(hass, NPSSO_DATA)
    renew.assert_awaited_once()
    assert entry.data["psn_refresh_token"] == "aus-npsso"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id)) is None
    assert hass.states.get("sensor.ps5_123_trophy_level").state == "312"


async def test_npsso_rejected_asks_once(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sony lehnt das gespeicherte NPSSO ab: Reparatur, die Cloud läuft bis zum Ablauf weiter, kein Dauerversuch."""
    mock_psn(aioclient_mock, refresh_expires_in=86400)
    aioclient_mock.get(psn.AUTHORIZE_URL, status=302, headers={"Location": "x://r?error=x"})
    entry = await _setup(hass, {**NPSSO_DATA, "psn_refresh_expires": time.time() + 86400})
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id))
    assert issue is not None and issue.translation_key == "psn_token_expiring"
    assert hass.states.get("sensor.ps5_123_trophy_level").state == "312"
    await entry.runtime_data.psn.async_refresh()
    assert _authorize_calls(aioclient_mock) == 1


async def test_npsso_unreachable_retries(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sony beim NPSSO-Tausch nicht erreichbar: nächste Runde wieder versuchen."""
    mock_psn(aioclient_mock, refresh_expires_in=86400)
    aioclient_mock.get(psn.AUTHORIZE_URL, exc=aiohttp.ClientError("weg"))
    entry = await _setup(hass, {**NPSSO_DATA, "psn_refresh_expires": time.time() + 86400})
    await entry.runtime_data.psn.async_refresh()
    assert _authorize_calls(aioclient_mock) == 2
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id)) is not None


async def test_options_enable_and_disable(
    hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_psn(aioclient_mock)
    entry = await _setup(hass, ENTRY_DATA)
    assert hass.states.get("sensor.ps5_123_trophy_level") is None  # ohne Cloud keine Cloud-Sensoren

    aioclient_mock.get(psn.AUTHORIZE_URL, status=302, headers={"Location": "x://r?code=c"})
    aioclient_mock.post(
        psn.TOKEN_URL, json={"access_token": "a", "refresh_token": "r1", "refresh_token_expires_in": 9e6}
    )
    aioclient_mock.get(psn.ACCOUNT_URL, json={"accountId": ACCOUNT})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"npsso": '{"npsso":"abc"}'})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["psn_npsso"] == "abc"
    assert entry.data["psn_refresh_token"] in ("r1", "refresh-2")
    assert hass.states.get("sensor.ps5_123_trophy_level") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"disable_cloud": True})
    await hass.async_block_till_done()
    assert "psn_refresh_token" not in entry.data
    assert "psn_npsso" not in entry.data


async def test_options_errors(hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker) -> None:
    mock_psn(aioclient_mock)
    entry = await _setup(hass, ENTRY_DATA)
    aioclient_mock.get(psn.AUTHORIZE_URL, status=302, headers={"Location": "x://r?error=x"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"npsso": "alt"})
    assert result["errors"] == {"npsso": "invalid_npsso"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"  # nichts geändert


async def test_firmware_update(hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker) -> None:
    mock_psn(aioclient_mock)
    await _setup(hass, ENTRY_DATA)
    update = hass.states.get("update.ps5_123_system_software")
    assert update.state == "on"  # 14.10.00 installiert, 14.20.00 verfügbar
    assert update.attributes["installed_version"] == "14.10.00"
    assert update.attributes["latest_version"] == "14.20.00"
    assert "Stabilität" in update.attributes["release_summary"]


def test_parse_info_page() -> None:
    fw = parse_info_page(INFO_PAGE, "u")
    assert fw is not None and (fw.version, fw.label) == ("14.20.00", "26.06-14.20.00")
    assert fw.notes == "Wir haben die Stabilität verbessert."
    assert parse_info_page("<p>nichts</p>", "u") is None
    assert local_version("14.10.00.03") == "14.10.00"
    assert local_version(None) is None


async def test_firmware_page_errors(hass: HomeAssistant, console: Console, aioclient_mock: AiohttpClientMocker) -> None:
    """Sony-Seite kaputt, ohne Versionsnummer oder nicht erreichbar: übersetzter Fehler, installiert bleibt sichtbar."""
    entry = await _setup(hass, ENTRY_DATA)
    coordinator = FirmwareCoordinator(hass, entry)
    url = INFO_URL.format(locale="en-us")
    for kwargs, key in (
        ({"status": 503}, "firmware_unreachable"),
        ({"text": "<p>Wartung</p>"}, "firmware_no_version"),
        ({"exc": aiohttp.ClientError()}, "firmware_unreachable"),
    ):
        aioclient_mock.clear_requests()
        aioclient_mock.get(url, **kwargs)
        with pytest.raises(UpdateFailed) as err:
            await coordinator._async_update_data()
        assert err.value.translation_key == key
    update = hass.states.get("update.ps5_123_system_software")
    assert update.attributes["installed_version"] == "14.10.00"
