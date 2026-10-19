"""Nachgebaute PS5 für Tests: DDP (UDP) und Remote Play (Kopplung + Steuer-Sitzung, TCP/UDP) auf localhost."""

from __future__ import annotations

import asyncio
from base64 import b64decode, b64encode
import os
import struct

from custom_components.ps5 import protocol as P

HOST_ID = "AABBCCDDEEFF"
REGIST_KEY = "6162636431323334"  # Hex-Text des Hex-Texts „abcd1234“
RP_KEY = "00112233445566778899aabbccddeeff"
PIN = "12345678"


class FakePS5:
    def __init__(self) -> None:
        self.on = False
        self.app: tuple[str, str] | None = None
        self.linking = True
        self.busy = False
        self.woken_with: str | None = None
        self.standby_received = False
        self.ddp_port = 0
        self.rp_port = 0
        self._servers: list[asyncio.AbstractServer] = []
        self._transports: list[asyncio.BaseTransport] = []

    # --- DDP ---
    def ddp_response(self) -> bytes:
        head = "HTTP/1.1 200 Ok" if self.on else "HTTP/1.1 620 Server Standby"
        lines = [head, f"host-id:{HOST_ID}", "host-type:PS5", "host-name:PS5-Test", "system-version:14100003"]
        if self.on and self.app:
            lines += [f"running-app-name:{self.app[0]}", f"running-app-titleid:{self.app[1]}"]
        return ("\n".join(lines) + "\n").encode()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        fake = self

        class DDP(asyncio.DatagramProtocol):
            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                self.transport = transport

            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                text = data.decode()
                if text.startswith("SRCH"):
                    self.transport.sendto(fake.ddp_response(), addr)  # type: ignore[attr-defined]
                elif text.startswith("WAKEUP"):
                    fields = dict(line.split(":", 1) for line in text.splitlines()[1:] if ":" in line)
                    fake.woken_with = fields.get("user-credential")
                    fake.on = True

        class RegistInit(asyncio.DatagramProtocol):
            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                self.transport = transport

            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                if data == b"SRC3" and fake.linking:
                    self.transport.sendto(b"RES3\x00\x00", addr)  # type: ignore[attr-defined]

        ddp, _ = await loop.create_datagram_endpoint(DDP, local_addr=("127.0.0.1", 0))
        self.ddp_port = ddp.get_extra_info("sockname")[1]
        server = await asyncio.start_server(self._handle_tcp, "127.0.0.1", 0)
        self.rp_port = server.sockets[0].getsockname()[1]
        udp, _ = await loop.create_datagram_endpoint(RegistInit, local_addr=("127.0.0.1", self.rp_port))
        self._servers.append(server)
        self._transports += [ddp, udp]

    async def stop(self) -> None:
        for transport in self._transports:
            transport.close()
        for server in self._servers:
            server.close()
            await server.wait_closed()

    # --- Remote Play (TCP) ---
    async def _handle_tcp(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            first = head.split(b"\r\n", 1)[0]
            if first.startswith(b"POST /sie/ps5/rp/sess/rgst"):
                await self._register(head, reader, writer)
            elif first.startswith(b"GET /sie/ps5/rp/sess/init"):
                self._nonce = os.urandom(16)
                ok = f"RP-Registkey: {REGIST_KEY}".encode() in head
                if self.busy:
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nRP-Application-Reason: 80108b10\r\n\r\n")
                elif ok:
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nRP-Nonce: " + b64encode(self._nonce) + b"\r\nContent-Length: 0\r\n\r\n"
                    )
                else:
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nRP-Application-Reason: 80108b02\r\n\r\n")
            elif first.startswith(b"GET /sie/ps5/rp/sess/ctrl"):
                await self._control(head, reader, writer)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def _register(self, head: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        length = int(next(line for line in head.split(b"\r\n") if line.startswith(b"Content-Length")).split(b": ")[1])
        body = await reader.readexactly(length)
        key_1 = body[401:409] + body[199:207]
        # nonce aus key_1 zurückrechnen (Umkehrung von P._reg_key_1)
        nonce = bytes(((key_1[i] + 45 - i) % 256) ^ P.REG_KEY_1[i * 32 + 8] for i in range(16))
        cipher = P.SessionCipher(P._reg_key_0(int(PIN)), nonce)
        decrypted = cipher.decrypt(body[480:]).decode(errors="replace")
        if "Np-AccountId:" not in decrypted or "Client-Type:" not in decrypted:
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        text = f"PS5-RegistKey: {REGIST_KEY}\r\nRP-Key: {RP_KEY}\r\nPS5-Nickname: PS5-Test\r\nPS5-Mac: {HOST_ID}\r\n"
        # Der Client entschlüsselt mit seinem dec-Zähler 0
        answer = P.SessionCipher(P._reg_key_0(int(PIN)), nonce)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n" + answer.encrypt(text.encode()))

    async def _control(self, head: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonce = self._nonce
        cipher = P.SessionCipher(P.rp_aes_key(nonce, bytes.fromhex(RP_KEY)), P.rp_nonce(nonce))
        headers = dict(line.split(": ", 1) for line in head.decode().split("\r\n")[1:] if ": " in line)
        auth = cipher.decrypt(b64decode(headers["RP-Auth"]))
        if auth != bytes.fromhex(REGIST_KEY) + bytes(8):
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        server = P.SessionCipher(P.rp_aes_key(nonce, bytes.fromhex(RP_KEY)), P.rp_nonce(nonce))
        server_type = server.encrypt((2).to_bytes(4, "little"))
        writer.write(b"HTTP/1.1 200 OK\r\nRP-Server-Type: " + b64encode(server_type) + b"\r\n\r\n")
        # erst ein Herzschlag, dann die Sitzungs-ID
        writer.write(struct.pack("!IHxx", 0, P.MSG_HEARTBEAT_REQUEST))
        session_id = server.encrypt(b"\x00\x00session-1234")
        writer.write(struct.pack("!IHxx", len(session_id), P.MSG_SESSION_ID) + session_id)
        await writer.drain()
        while True:
            frame = await reader.readexactly(8)
            length, msg_type = struct.unpack("!IH", frame[:6])
            if length:
                await reader.readexactly(length)
            if msg_type == P.MSG_STANDBY:
                self.standby_received = True
                self.on = False
                return
