"""Protokoll gegen die nachgebaute PS5: Status, Suche, Aufwecken, Kopplung, Ruhemodus."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from custom_components.ps5 import protocol as P

from .fake_ps5 import HOST_ID, PIN, REGIST_KEY, RP_KEY, FakePS5

# Echte UDP/TCP-Sockets auf localhost: die HA-Testumgebung sperrt Sockets beim Testaufbau,
# das Fixture socket_enabled (pytest-socket) hebt die Sperre danach auf. Der Marker
# enable_socket reichte unter Linux (GitHub Actions) nicht, nur unter macOS.
pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
async def ps5(monkeypatch: pytest.MonkeyPatch, socket_enabled: None) -> AsyncIterator[FakePS5]:
    fake = FakePS5()
    await fake.start()
    monkeypatch.setattr(P, "DDP_PORT", fake.ddp_port)
    monkeypatch.setattr(P, "RP_PORT", fake.rp_port)
    yield fake
    await fake.stop()


async def test_status(ps5: FakePS5) -> None:
    status = await P.async_get_status("127.0.0.1")
    assert status is not None and not status.on
    assert (status.host_id, status.mac, status.firmware) == (HOST_ID, "aa:bb:cc:dd:ee:ff", "14.10.00.03")
    ps5.on, ps5.app = True, ("ASTRO BOT", "PPSA01325")
    status = await P.async_get_status("127.0.0.1")
    assert status is not None and status.on and (status.app_name, status.title_id) == ("ASTRO BOT", "PPSA01325")


async def test_no_answer_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(P, "DDP_PORT", 9)  # niemand antwortet
    assert await P.async_get_status("127.0.0.1", seconds=0.2) is None


async def test_wakeup(ps5: FakePS5) -> None:
    await P.async_wakeup("127.0.0.1", REGIST_KEY)
    await P.async_get_status("127.0.0.1")
    assert ps5.on
    assert ps5.woken_with == str(int("abcd1234", 16))


async def test_register(ps5: FakePS5) -> None:
    reg = await P.async_register("127.0.0.1", "1234567890123456789", PIN)
    assert (reg.regist_key, reg.rp_key, reg.nickname) == (REGIST_KEY, RP_KEY, "PS5-Test")


async def test_register_not_in_link_mode(ps5: FakePS5) -> None:
    ps5.linking = False
    with pytest.raises(P.PS5Error):
        await P.async_register("127.0.0.1", "1234567890123456789", PIN, seconds=0.3)


async def test_standby(ps5: FakePS5) -> None:
    ps5.on = True
    await P.async_standby("127.0.0.1", REGIST_KEY, RP_KEY)
    assert ps5.standby_received


async def test_standby_unknown_key(ps5: FakePS5) -> None:
    with pytest.raises(P.PS5AuthError):
        await P.async_standby("127.0.0.1", "3030303030303030", RP_KEY)


def test_account_id() -> None:
    assert P.account_id_b64("1234567890123456789") == P.account_id_b64(P.account_id_b64("1234567890123456789"))
    with pytest.raises(ValueError, match="Konto-ID"):
        P.account_id_b64("keine-id")


def test_parse_ddp_ignores_others() -> None:
    assert P.parse_ddp(b"SRCH * HTTP/1.1\n") is None
    assert P.parse_ddp(b"HTTP/1.1 200 Ok\nhost-type:PS4\nhost-id:AA\n") is None
    assert P.parse_ddp(b"\xff\xfe") is None


async def test_standby_busy_is_not_auth(ps5: FakePS5) -> None:
    ps5.busy = True
    with pytest.raises(P.PS5BusyError):
        await P.async_standby("127.0.0.1", REGIST_KEY, RP_KEY)


async def test_read_head_rejects_garbage() -> None:
    """Kein HTTP (Statuscode keine Zahl) oder endloser Kopf → PS5Error statt ValueError/LimitOverrunError."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.1 abc Unsinn\r\n\r\n")
    with pytest.raises(P.PS5Error, match="unlesbar"):
        await P._read_head(reader, 1)
    reader = asyncio.StreamReader(limit=32)
    reader.feed_data(b"x" * 100)
    with pytest.raises(P.PS5Error, match="zu lang"):
        await P._read_head(reader, 1)
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.1 200 Ok\r\nRP-Nonce: abc\r\n\r\n")
    assert await P._read_head(reader, 1) == (200, {"rp-nonce": "abc"})
