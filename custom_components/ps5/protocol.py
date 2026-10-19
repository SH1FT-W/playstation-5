"""Lokales PS5-Protokoll (ohne HA, testbar): Status, Aufwecken, Kopplung, Ruhemodus.

- Status/Aufwecken: DDP, UDP 9302 – Textnachrichten wie HTTP („SRCH * HTTP/1.1“).
- Kopplung und Ruhemodus: Remote Play, Port 9295 – Kopplung mit der 8-stelligen PIN aus
  Einstellungen → System → Remote Play → Gerät verknüpfen, Ruhemodus über eine kurze Steuer-Sitzung.

Nachgebaut nach pyremoteplay (ktnrg45, GPLv3) bzw. chiaki; Schlüsseltabellen in keys.py.
"""

from __future__ import annotations

import asyncio
from base64 import b64decode, b64encode
from dataclasses import dataclass
import hashlib
import hmac
import logging
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

try:  # cryptography ≥ 43: CFB liegt unter „decrepit“, der alte Ort entfällt mit 49
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:  # pragma: no cover
    from cryptography.hazmat.primitives.ciphers.modes import CFB

from .keys import HMAC_KEY, REG_KEY_0, REG_KEY_1, SESSION_KEY_0, SESSION_KEY_1

_LOGGER = logging.getLogger(__name__)

DDP_PORT = 9302
RP_PORT = 9295
DDP_VERSION = "00030010"
RP_VERSION = "1.0"
USER_AGENT = "remoteplay Windows"
OS_TYPE = b"Win10.0.0"
CLIENT_TYPE = "dabfa2ec873de5839bee8d3f4c0239c4282c07c25c6077a2931afcf0adc0d34f"
DID_PREFIX = b"\x00\x18\x00\x00\x00\x07\x00\x40\x00\x80"
STREAM_TYPE_H264 = 1

MSG_SESSION_ID = 0x33
MSG_HEARTBEAT_REQUEST = 0xFE
MSG_HEARTBEAT_RESPONSE = 0x1FE
MSG_STANDBY = 0x50
HEARTBEAT_RESPONSE = b"\x00\x00\x00\x00\x01\xfe\x00\x00"


class PS5Error(Exception):
    """PS5 nicht erreichbar oder unerwartete Antwort."""


class PS5AuthError(PS5Error):
    """Kopplung ungültig (Gerät in der PS5 entfernt) oder falsche PIN."""


class PS5BusyError(PS5Error):
    """Remote Play gerade belegt (0x80108B10) – auch durch einen eigenen, eben abgebrochenen Versuch; ~1 Min."""


# RP-Application-Reason der PS5 (aus pyremoteplay/chiaki)
RP_INVALID_PSN_ID = "80108b02"
RP_REGIST_FAILED = "80108b09"
RP_IN_USE = "80108b10"


@dataclass(frozen=True, slots=True)
class PS5Status:
    """Antwort auf die DDP-Suche."""

    on: bool  # 200 = an, 620 = Ruhemodus
    host_id: str  # MAC ohne Trennzeichen, z. B. AABBCCDDEEFF
    host_name: str
    host_type: str
    system_version: str
    app_name: str | None
    title_id: str | None

    @property
    def mac(self) -> str:
        return ":".join(self.host_id[i : i + 2] for i in range(0, 12, 2)).lower()

    @property
    def firmware(self) -> str:
        """system-version 14100003 → 14.10.00.03."""
        v = self.system_version
        return f"{int(v[0:2])}.{v[2:4]}.{v[4:6]}.{v[6:8]}" if len(v) == 8 and v.isdigit() else v


@dataclass(frozen=True, slots=True)
class Registration:
    """Ergebnis der Kopplung; regist_key/rp_key reichen für Aufwecken und Ruhemodus."""

    regist_key: str  # Hex-Text, wie die PS5 ihn liefert
    rp_key: str  # Hex-Text (16 Bytes)
    nickname: str
    mac: str


def account_id_b64(account_id: str) -> str:
    """PSN-Konto-ID (Dezimalzahl wie in der PSN-Integration) → Base64 der 8 Bytes (little endian)."""
    account_id = account_id.strip()
    if account_id.isdigit():
        return b64encode(int(account_id).to_bytes(8, "little")).decode()
    try:
        if len(b64decode(account_id, validate=True)) == 8:
            return account_id  # schon im Remote-Play-Format
    except ValueError:
        pass
    raise ValueError("ungültige PSN-Konto-ID")


# --- DDP (Status, Aufwecken) -------------------------------------------------------------------------------


def ddp_message(msg_type: str, data: dict[str, str] | None = None) -> bytes:
    lines = [f"{msg_type} * HTTP/1.1"]
    lines += [f"{key}:{value}" for key, value in (data or {}).items()]
    lines.append(f"device-discovery-protocol-version:{DDP_VERSION}")
    return ("\n".join(lines) + "\n").encode()


def parse_ddp(data: bytes) -> PS5Status | None:
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if not lines or not lines[0].startswith("HTTP/1.1 "):
        return None
    code = lines[0].split(" ", 2)[1]
    fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
    if fields.get("host-type", "").upper() != "PS5" or "host-id" not in fields:
        return None
    return PS5Status(
        on=code == "200",
        host_id=fields["host-id"].upper(),
        host_name=fields.get("host-name", "PS5"),
        host_type="PS5",
        system_version=fields.get("system-version", ""),
        app_name=fields.get("running-app-name") or None,
        title_id=fields.get("running-app-titleid") or None,
    )


class _DDPProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.responses: asyncio.Queue[tuple[bytes, str]] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.responses.put_nowait((data, addr[0]))


async def _ddp_exchange(host: str, message: bytes, *, wait: float, broadcast: bool = False) -> list[tuple[bytes, str]]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _DDPProtocol, local_addr=("0.0.0.0", 0), allow_broadcast=broadcast
    )
    try:
        transport.sendto(message, (host, DDP_PORT))
        if wait <= 0:
            return []
        results: list[tuple[bytes, str]] = []
        deadline = loop.time() + wait
        while (remaining := deadline - loop.time()) > 0:
            try:
                results.append(await asyncio.wait_for(protocol.responses.get(), remaining))
            except TimeoutError:
                break
            if not broadcast:
                break
        return results
    finally:
        transport.close()


async def async_get_status(host: str, seconds: float = 2.0) -> PS5Status | None:
    """Status der PS5; None = keine Antwort (komplett aus oder nicht erreichbar)."""
    for _attempt in range(2):  # UDP: ein verlorenes Paket nicht gleich als „aus“ werten
        for data, _addr in await _ddp_exchange(host, ddp_message("SRCH"), wait=seconds / 2):
            if status := parse_ddp(data):
                return status
    return None


async def async_discover(seconds: float = 2.0) -> dict[str, PS5Status]:
    """PS5-Konsolen im Netz per Broadcast finden → {IP: Status}."""
    found: dict[str, PS5Status] = {}
    for data, addr in await _ddp_exchange("255.255.255.255", ddp_message("SRCH"), wait=seconds, broadcast=True):
        if status := parse_ddp(data):
            found[addr] = status
    return found


def wake_credential(regist_key: str) -> str:
    """RegistKey (Hex-Text eines Hex-Texts) → Dezimalzahl für „user-credential“."""
    return str(int.from_bytes(bytes.fromhex(bytes.fromhex(regist_key).decode()), "big"))


async def async_wakeup(host: str, regist_key: str) -> None:
    message = ddp_message(
        "WAKEUP",
        {
            "user-credential": wake_credential(regist_key),
            "client-type": "vr",
            "auth-type": "R",
            "model": "w",
            "app-type": "r",
        },
    )
    await _ddp_exchange(host, message, wait=0)


# --- Verschlüsselung (AES-CFB-128, IV aus HMAC-SHA256) -----------------------------------------------------


def _aes_iv(nonce: bytes, counter: int) -> bytes:
    return hmac.new(HMAC_KEY, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()[:16]


class SessionCipher:
    """Ein Strom je Nachricht: nach jeder Nachricht zählt der Zähler hoch und der IV wird neu berechnet."""

    def __init__(self, key: bytes, nonce: bytes) -> None:
        self._key = key
        self._nonce = nonce
        self.enc_counter = 0
        self.dec_counter = 0

    def _run(self, data: bytes, counter: int, *, encrypt: bool) -> bytes:
        cipher = Cipher(algorithms.AES(self._key), CFB(_aes_iv(self._nonce, counter)))
        ctx = cipher.encryptor() if encrypt else cipher.decryptor()
        return ctx.update(data) + ctx.finalize()

    def encrypt(self, data: bytes) -> bytes:
        result = self._run(data, self.enc_counter, encrypt=True)
        self.enc_counter += 1
        return result

    def decrypt(self, data: bytes) -> bytes:
        result = self._run(data, self.dec_counter, encrypt=False)
        self.dec_counter += 1
        return result


# --- Kopplung ----------------------------------------------------------------------------------------------


def _reg_key_0(pin: int) -> bytes:
    key = bytearray(REG_KEY_0[i * 32 + 1] for i in range(16))
    for shift, index in enumerate(range(12, 16)):
        key[index] ^= (pin >> (24 - shift * 8)) & 0xFF
    return bytes(key)


def _reg_key_1(nonce: bytes) -> bytes:
    return bytes(((nonce[i] ^ REG_KEY_1[i * 32 + 8]) - 45 + i) % 256 for i in range(16))


def build_registration(account_b64: str, pin: str, nonce: bytes) -> tuple[SessionCipher, bytes]:
    """Kopplungs-Anfrage (Kopf + Nutzdaten) und die Verschlüsselung, mit der die Antwort entschlüsselt wird."""
    key_1 = _reg_key_1(nonce)
    filler = b"A" * 480
    payload = filler[0:199] + key_1[8:] + filler[207:401] + key_1[0:8] + filler[409:]
    cipher = SessionCipher(_reg_key_0(int(pin)), nonce)
    payload += cipher.encrypt(f"Client-Type: {CLIENT_TYPE}\r\nNp-AccountId: {account_b64}\r\n".encode())
    head = (
        # Die PS5 erwartet genau diese (kaputte) Anfragezeile
        "POST /sie/ps5/rp/sess/rgst HTTP/1.1\r\n HTTP/1.1\r\n"
        "HOST: 10.0.2.15\r\n"
        f"User-Agent: {USER_AGENT}\r\n"
        "Connection: close\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"RP-Version: {RP_VERSION}\r\n\r\n"
    ).encode()
    return cipher, head + payload


def parse_registration(cipher: SessionCipher, response: bytes) -> Registration:
    parts = response.split(b"\r\n")
    if not parts or b"200 OK" not in parts[0]:
        raise PS5AuthError(f"Kopplung abgelehnt: {parts[0][:60]!r}" if parts else "keine Antwort")
    body = cipher.decrypt(parts[-1]).decode(errors="replace")
    info = dict(line.split(": ", 1) for line in body.split("\r\n") if ": " in line)
    if "PS5-RegistKey" not in info or "RP-Key" not in info:
        raise PS5Error("Kopplung: unvollständige Antwort")
    return Registration(
        regist_key=info["PS5-RegistKey"],
        rp_key=info["RP-Key"],
        nickname=info.get("PS5-Nickname", "PS5"),
        mac=info.get("PS5-Mac", ""),
    )


class _RegistInitProtocol(asyncio.DatagramProtocol):
    def __init__(self, future: asyncio.Future[bytes]) -> None:
        self.future = future

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self.future.done():
            self.future.set_result(data)


async def async_register(host: str, account_id: str, pin: str, seconds: float = 5.0) -> Registration:
    """Mit PIN koppeln. Die PS5 muss gerade „Gerät verknüpfen“ anzeigen."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()
    transport, _ = await loop.create_datagram_endpoint(lambda: _RegistInitProtocol(future), local_addr=("0.0.0.0", 0))
    try:
        transport.sendto(b"SRC3", (host, RP_PORT))
        try:
            answer = await asyncio.wait_for(future, seconds)
        except TimeoutError as err:
            raise PS5Error("PS5 ist nicht im Kopplungsmodus") from err
    finally:
        transport.close()
    if not answer.startswith(b"RES3"):
        raise PS5Error("unerwartete Antwort auf die Kopplungsanfrage")

    cipher, request = build_registration(account_id_b64(account_id), pin, os.urandom(16))
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, RP_PORT), seconds)
        try:
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(1024), seconds)
        finally:
            writer.close()
    except (OSError, TimeoutError) as err:
        raise PS5Error(f"Kopplung: {err}") from err
    return parse_registration(cipher, response)


# --- Steuer-Sitzung (Ruhemodus) ----------------------------------------------------------------------------


def rp_nonce(nonce: bytes) -> bytes:
    key = SESSION_KEY_0[(nonce[0] >> 3) * 112 :]
    return bytes(((nonce[i] - 45 - i) ^ key[i]) % 256 for i in range(16))


def rp_aes_key(nonce: bytes, rp_key: bytes) -> bytes:
    key = SESSION_KEY_1[(nonce[7] >> 3) * 112 :]
    return bytes(((rp_key[i] + 24 + i) ^ nonce[i] ^ key[i]) % 256 for i in range(16))


def session_headers(host: str, regist_key: str, cipher: SessionCipher) -> dict[str, str]:
    """Kopfzeilen der Steuer-Sitzung; die Reihenfolge der encrypt-Aufrufe ist Teil des Protokolls."""

    def enc(data: bytes) -> str:
        return b64encode(cipher.encrypt(data)).decode()

    auth = enc(bytes.fromhex(regist_key) + bytes(8))
    did = enc(DID_PREFIX + os.urandom(16) + bytes(6))
    os_type = enc(OS_TYPE.ljust(10, b"\x00"))
    bitrate = enc(bytes(4))
    stream_type = enc(STREAM_TYPE_H264.to_bytes(4, "little"))
    return {
        "Host": f"{host}:{RP_PORT}",
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
        "Content-Length": "0",
        "RP-Auth": auth,
        "RP-Version": RP_VERSION,
        "RP-Did": did,
        "RP-ControllerType": "3",
        "RP-ClientType": "11",
        "RP-OSType": os_type,
        "RP-ConPath": "1",
        "RP-StartBitrate": bitrate,
        "RP-StreamingType": stream_type,
    }


def _http_get(path: str, headers: dict[str, str]) -> bytes:
    lines = [f"GET {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in headers.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


async def _read_head(reader: asyncio.StreamReader, seconds: float) -> tuple[int, dict[str, str]]:
    """HTTP-Kopf lesen. Unsinn (kein HTTP, Statuscode keine Zahl, endloser Kopf) wird ein PS5Error."""
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), seconds)
    except asyncio.LimitOverrunError as err:
        raise PS5Error("Antwort der PS5 unlesbar (Kopf zu lang)") from err
    lines = raw.decode(errors="replace").split("\r\n")
    parts = lines[0].split(" ")
    try:
        status = int(parts[1]) if len(parts) > 1 else 0
    except ValueError as err:
        raise PS5Error(f"Antwort der PS5 unlesbar ({lines[0]!r})") from err
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key.lower()] = value
    return status, headers


def build_message(cipher: SessionCipher, msg_type: int, payload: bytes = b"") -> bytes:
    # Wie im Original: der Zähler läuft mit, die Nutzdaten gehen unverschlüsselt raus
    cipher.encrypt(payload)
    return struct.pack("!IHxx", len(payload), msg_type) + payload


async def async_standby(host: str, regist_key: str, rp_key: str, seconds: float = 5.0) -> None:
    """Steuer-Sitzung öffnen, auf die Sitzungs-ID warten, Ruhemodus senden."""
    base = {"Host": f"{host}:{RP_PORT}", "User-Agent": USER_AGENT, "Content-Length": "0", "RP-Version": RP_VERSION}
    try:
        # 1. Init: liefert die Nonce
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, RP_PORT), seconds)
        try:
            writer.write(
                _http_get("/sie/ps5/rp/sess/init", {**base, "Connection": "close", "RP-Registkey": regist_key})
            )
            await writer.drain()
            status, headers = await _read_head(reader, seconds)
        finally:
            writer.close()
        if status != 200 or "rp-nonce" not in headers:
            reason = headers.get("rp-application-reason", "").lower()
            if reason == RP_IN_USE:
                raise PS5BusyError("Remote Play gerade belegt")
            if reason in (RP_INVALID_PSN_ID, RP_REGIST_FAILED) or (status in (401, 403) and not reason):
                raise PS5AuthError(f"Sitzung abgelehnt (HTTP {status} {reason})")
            raise PS5Error(f"Sitzung abgelehnt (HTTP {status} {reason})")
        nonce = b64decode(headers["rp-nonce"])
        cipher = SessionCipher(rp_aes_key(nonce, bytes.fromhex(rp_key)), rp_nonce(nonce))

        # 2. Steuer-Sitzung: dieselbe TCP-Verbindung trägt danach die Nachrichten
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, RP_PORT), seconds)
        try:
            writer.write(_http_get("/sie/ps5/rp/sess/ctrl", session_headers(host, regist_key, cipher)))
            await writer.drain()
            status, headers = await _read_head(reader, seconds)
            if status != 200 or "rp-server-type" not in headers:
                raise PS5AuthError(f"Steuer-Sitzung abgelehnt (HTTP {status})")
            cipher.decrypt(b64decode(headers["rp-server-type"]))

            deadline = asyncio.get_running_loop().time() + seconds
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise PS5Error("keine Sitzungs-ID von der PS5")
                head = await asyncio.wait_for(reader.readexactly(8), remaining)
                length, msg_type = struct.unpack("!IH", head[:6])
                payload = await asyncio.wait_for(reader.readexactly(length), remaining) if length else b""
                if payload:
                    cipher.decrypt(payload)
                if msg_type == MSG_HEARTBEAT_REQUEST:
                    writer.write(build_message(cipher, MSG_HEARTBEAT_RESPONSE, HEARTBEAT_RESPONSE))
                elif msg_type == MSG_SESSION_ID:
                    break
            writer.write(build_message(cipher, MSG_STANDBY))
            await writer.drain()
            # Die PS5 beendet die Verbindung, sobald sie in den Ruhemodus geht
            try:
                await asyncio.wait_for(reader.read(), seconds)
            except TimeoutError:
                _LOGGER.debug("PS5 hat die Sitzung nach dem Ruhemodus-Befehl nicht beendet")
        finally:
            writer.close()
    except (OSError, asyncio.IncompleteReadError, TimeoutError) as err:
        raise PS5Error(f"Ruhemodus: {err}") from err
