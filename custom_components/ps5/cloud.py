"""Optionaler PSN-Cloud-Teil: Präsenz (laufendes Spiel, Online-Status) jede Minute, Trophäen/Profil alle 15 Minuten.

Fällt die Cloud aus, bleiben nur diese Entitäten unbekannt – Steuerung und An/Aus laufen lokal weiter.
Läuft das Erneuerungs-Token ab (oder bald ab), meldet eine Reparatur mit Einfügefeld das neue NPSSO an.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_PSN_ACCOUNT_ID, CONF_PSN_EXPIRES, CONF_PSN_TOKEN, DOMAIN
from .psn import PSNClient, PSNError, PSNTokenError, async_account_id_for_token, async_token_from_npsso

if TYPE_CHECKING:
    from .coordinator import PS5ConfigEntry

_LOGGER = logging.getLogger(__name__)

CLOUD_INTERVAL = timedelta(seconds=60)
# Trophäen und Profil ändern sich selten: jede 15. Runde
SLOW_EVERY = 15
# So früh vor Ablauf des Erneuerungs-Tokens erscheint die Reparatur
WARN_BEFORE = 7 * 24 * 3600
SSO_URL = "https://ca.account.sony.com/api/v1/ssocookie"


def issue_id(entry_id: str) -> str:
    return f"psn_token_{entry_id}"


@dataclass(slots=True)
class PSNData:
    presence: dict[str, Any] = field(default_factory=dict)
    trophies: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)

    @property
    def game(self) -> dict[str, Any]:
        titles = self.presence.get("gameTitleInfoList") or []
        return dict(titles[0]) if titles else {}

    @property
    def platform(self) -> str | None:
        return (self.presence.get("primaryPlatformInfo") or {}).get("platform")

    @property
    def playing_on_ps5(self) -> bool:
        info = self.presence.get("primaryPlatformInfo") or {}
        return (
            bool(self.game) and str(info.get("platform", "")).upper() == "PS5" and info.get("onlineStatus") == "online"
        )

    @property
    def cover_url(self) -> str | None:
        game = self.game
        return game.get("conceptIconUrl") or game.get("npTitleIconUrl") or None

    @property
    def avatar_url(self) -> str | None:
        avatars = self.profile.get("avatars") or []
        order = {"xl": 0, "l": 1, "m": 2, "s": 3}
        best = min(avatars, key=lambda a: order.get(str(a.get("size", "")).lower(), 9), default=None)
        return best.get("url") if best else None


class PSNCoordinator(DataUpdateCoordinator[PSNData]):
    def __init__(self, hass: HomeAssistant, entry: PS5ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_psn", update_interval=CLOUD_INTERVAL)
        self._entry = entry
        self.client = PSNClient(
            async_get_clientsession(hass),
            str(entry.data[CONF_PSN_ACCOUNT_ID]),
            str(entry.data[CONF_PSN_TOKEN]),
            self._save_token,
        )
        self.client.refresh_expires_at = entry.data.get(CONF_PSN_EXPIRES)
        self._round = 0

    @callback
    def _save_token(self, token: dict[str, Any]) -> None:
        """Neuestes Erneuerungs-Token im Eintrag ablegen (ohne Neuladen)."""
        data = dict(self._entry.data)
        if (
            data.get(CONF_PSN_TOKEN) == token["refresh_token"]
            and data.get(CONF_PSN_EXPIRES) == token["refresh_expires_at"]
        ):
            return
        data[CONF_PSN_TOKEN] = token["refresh_token"]
        data[CONF_PSN_EXPIRES] = token["refresh_expires_at"]
        self.hass.config_entries.async_update_entry(self._entry, data=data)

    def _issue(self, *, expired: bool) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id(self._entry.entry_id),
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR if expired else ir.IssueSeverity.WARNING,
            translation_key="psn_token_expired" if expired else "psn_token_expiring",
            translation_placeholders={"name": self._entry.title, "sso_url": SSO_URL},
            data={"entry_id": self._entry.entry_id},
        )

    async def _async_update_data(self) -> PSNData:
        old = self.data
        try:
            data = PSNData(presence=await self.client.presence())
            if old is None or self._round % SLOW_EVERY == 0:
                data.trophies = await self.client.trophies()
                data.profile = await self.client.profile()
            else:
                data.trophies, data.profile = old.trophies, old.profile
        except PSNTokenError as err:
            self._issue(expired=True)
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="psn_expired") from err
        except PSNError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="psn_unreachable",
                translation_placeholders={"error": str(err)},
            ) from err
        expires = self.client.refresh_expires_at
        if expires is not None and expires - time.time() < WARN_BEFORE:
            self._issue(expired=False)
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(self._entry.entry_id))
        self._round += 1
        return data


async def async_activate(hass: HomeAssistant, entry: PS5ConfigEntry, npsso: str) -> None:
    """NPSSO → Erneuerungs-Token + Konto-ID im Eintrag speichern und neu laden (Optionen und Reparatur)."""
    session = async_get_clientsession(hass)
    token = await async_token_from_npsso(session, npsso)
    account_id = await async_account_id_for_token(session, str(token["access_token"]))
    expires_in = token.get("refresh_token_expires_in")
    data = {
        **entry.data,
        CONF_PSN_ACCOUNT_ID: account_id,
        CONF_PSN_TOKEN: token["refresh_token"],
        CONF_PSN_EXPIRES: time.time() + float(expires_in) if expires_in else None,
    }
    hass.config_entries.async_update_entry(entry, data=data)
    ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
    hass.config_entries.async_schedule_reload(entry.entry_id)


def deactivate(hass: HomeAssistant, entry: PS5ConfigEntry) -> None:
    data = {k: v for k, v in entry.data.items() if k not in (CONF_PSN_ACCOUNT_ID, CONF_PSN_TOKEN, CONF_PSN_EXPIRES)}
    hass.config_entries.async_update_entry(entry, data=data)
    ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
    hass.config_entries.async_schedule_reload(entry.entry_id)
