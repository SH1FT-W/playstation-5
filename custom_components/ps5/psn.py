"""PSN-Konto-ID einmalig aus einem NPSSO-Token holen (nur bei der Einrichtung, danach läuft alles lokal).

Gleicher Weg wie PSNAWP bzw. die HA-Integration „PlayStation Network“: NPSSO-Cookie → Autorisierungscode →
Zugriffstoken → /v1/devices/accounts/me. Das Token wird nicht gespeichert.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
import uuid

import aiohttp

AUTHORIZE_URL = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
TOKEN_URL = "https://ca.account.sony.com/api/authz/v3/oauth/token"
ACCOUNT_URL = "https://dms.api.playstation.com/api/v1/devices/accounts/me"
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
# Öffentliche Kennung der PS-App (base64 von client_id:secret), wie in PSNAWP
BASIC_AUTH = "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
SCOPE = "psn:mobile.v2.core psn:clientapp"
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"


class PSNError(Exception):
    """Sony nicht erreichbar oder unerwartete Antwort."""


class PSNTokenError(PSNError):
    """NPSSO-Token falsch oder abgelaufen."""


def clean_npsso(value: str) -> str:
    """Erlaubt auch das ganze kopierte JSON {"npsso":"…"}."""
    value = value.strip()
    if value.startswith("{") and '"npsso"' in value:
        value = value.split('"npsso"', 1)[1].split('"')[1]
    return value


TOKEN_HEADERS = {
    "Authorization": BASIC_AUTH,
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "com.sony.snei.np.android.sso.share.oauth.versa.USER_AGENT",
}
API_HEADERS = {"Accept-Language": "de-DE,de;q=0.9", "Country": "DE", "User-Agent": "Mozilla/5.0 (Linux; Android 14)"}
PROFILE_URL = "https://m.np.playstation.com/api/userProfile/v1/internal/users/{account_id}/profiles"
PRESENCE_URL = "https://m.np.playstation.com/api/userProfile/v2/internal/users/{account_id}/basicPresences"
TROPHY_URL = "https://m.np.playstation.com/api/trophy/v1/users/{account_id}/trophySummary"


async def async_account_id(session: aiohttp.ClientSession, npsso: str) -> str:
    """Nur die Konto-ID (Kopplung)."""
    token = await async_token_from_npsso(session, npsso)
    return await async_account_id_for_token(session, str(token["access_token"]))


async def async_token_from_npsso(session: aiohttp.ClientSession, npsso: str) -> dict[str, Any]:
    """NPSSO → Token-Antwort (access_token, refresh_token, expires_in, refresh_token_expires_in)."""
    cid = str(uuid.uuid4())
    try:
        async with asyncio.timeout(20):
            resp = await session.get(
                AUTHORIZE_URL,
                # Parameter und Kopfzeilen 1:1 wie PSNAWP 3.0.3 (Authenticator.get_authorization_code)
                params={
                    "access_type": "offline",
                    "cid": cid,
                    "client_id": CLIENT_ID,
                    "device_base_font_size": "10",
                    "device_profile": "mobile",
                    "elements_visibility": "no_aclink",
                    "enable_scheme_error_code": "true",
                    "no_captcha": "true",
                    "PlatformPrivacyWs1": "minimal",
                    "redirect_uri": REDIRECT_URI,
                    "response_type": "code",
                    "scope": SCOPE,
                    "service_entity": "urn:service-entity:psn",
                    "service_logo": "ps",
                    "smcid": "psapp:signin",
                    "support_scheme": "sneiprls",
                    "turnOnTrustedBrowser": "true",
                    "ui": "pr",
                },
                headers={
                    "Cookie": f"npsso={clean_npsso(npsso)}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "com.scee.psxandroid",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-site",
                    "Sec-Fetch-User": "?1",
                },
                allow_redirects=False,
            )
            async with resp:
                location = resp.headers.get("Location", "")
            query = parse_qs(urlparse(location).query)
            if "code" not in query:
                raise PSNTokenError("NPSSO abgelehnt")

            resp = await session.post(
                TOKEN_URL,
                headers=TOKEN_HEADERS | {"X-Psn-Correlation-Id": cid},
                data={
                    "cid": cid,
                    "code": query["code"][0],
                    "grant_type": "authorization_code",
                    "redirect_uri": REDIRECT_URI,
                    "scope": SCOPE,
                    "token_format": "jwt",
                },
            )
            async with resp:
                if resp.status != 200:
                    raise PSNError(f"Token: HTTP {resp.status}")
                token: dict[str, Any] = await resp.json(content_type=None)

    except (TimeoutError, aiohttp.ClientError, KeyError, ValueError) as err:
        raise PSNError(str(err)) from err
    if "access_token" not in token:
        raise PSNError("keine Token-Antwort")
    return token


async def async_account_id_for_token(session: aiohttp.ClientSession, access_token: str) -> str:
    try:
        async with asyncio.timeout(20):
            resp = await session.get(ACCOUNT_URL, headers={"Authorization": f"Bearer {access_token}"})
            async with resp:
                if resp.status != 200:
                    raise PSNError(f"Konto: HTTP {resp.status}")
                account: dict[str, Any] = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        raise PSNError(str(err)) from err
    account_id = str(account.get("accountId", ""))
    if not account_id.isdigit():
        raise PSNError("keine Konto-ID in der Antwort")
    return account_id


class PSNClient:
    """Cloud-Teil nach der Einrichtung: hält das Zugriffstoken frisch und liefert Präsenz, Trophäen, Profil.

    Gespeichert wird nur das Erneuerungs-Token. Sony gibt bei jeder Erneuerung ein neues mit; `on_new_token`
    schreibt es zurück in den Konfigurationseintrag, damit HA immer das neueste hat.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        account_id: str,
        refresh_token: str,
        on_new_token: Callable[[dict[str, Any]], None],
    ) -> None:
        self._session = session
        self.account_id = account_id
        self._refresh_token = refresh_token
        self._on_new_token = on_new_token
        self._access_token: str | None = None
        self._access_until = 0.0
        self.refresh_expires_at: float | None = None

    async def _token(self) -> str:
        if self._access_token and time.time() < self._access_until - 60:
            return self._access_token
        try:
            async with asyncio.timeout(20):
                resp = await self._session.post(
                    TOKEN_URL,
                    headers=TOKEN_HEADERS,
                    data={
                        "refresh_token": self._refresh_token,
                        "grant_type": "refresh_token",
                        "scope": SCOPE,
                        "token_format": "jwt",
                    },
                )
                async with resp:
                    if resp.status in (400, 401):
                        raise PSNTokenError("Erneuerungs-Token abgelaufen")
                    if resp.status != 200:
                        raise PSNError(f"Token: HTTP {resp.status}")
                    token: dict[str, Any] = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise PSNError(str(err)) from err
        self.store(token)
        return str(token["access_token"])

    def store(self, token: dict[str, Any]) -> None:
        """Neue Token-Antwort übernehmen (auch direkt nach der Einrichtung)."""
        now = time.time()
        self._access_token = str(token["access_token"])
        self._access_until = now + float(token.get("expires_in", 3600))
        if token.get("refresh_token"):
            self._refresh_token = str(token["refresh_token"])
        if token.get("refresh_token_expires_in"):
            self.refresh_expires_at = now + float(token["refresh_token_expires_in"])
        self._on_new_token({"refresh_token": self._refresh_token, "refresh_expires_at": self.refresh_expires_at})

    async def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        headers = API_HEADERS | {"Authorization": f"Bearer {await self._token()}"}
        try:
            async with asyncio.timeout(20):
                resp = await self._session.get(url, params=params, headers=headers)
                async with resp:
                    if resp.status == 401:
                        self._access_token = None
                        raise PSNError("Zugriff abgelaufen")
                    if resp.status != 200:
                        raise PSNError(f"HTTP {resp.status}")
                    data: dict[str, Any] = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise PSNError(str(err)) from err
        return data

    async def presence(self) -> dict[str, Any]:
        data = await self._get(
            PRESENCE_URL.format(account_id=self.account_id),
            {"type": "primary", "platforms": "PS4,PS5,MOBILE_APP,PSPC", "withOwnGameTitleInfo": "true"},
        )
        return dict(data.get("basicPresence") or {})

    async def trophies(self) -> dict[str, Any]:
        return await self._get(TROPHY_URL.format(account_id=self.account_id))

    async def profile(self) -> dict[str, Any]:
        data = await self._get(PROFILE_URL.format(account_id=self.account_id))
        return dict(data)
