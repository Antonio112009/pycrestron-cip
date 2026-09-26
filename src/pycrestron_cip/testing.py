"""A fake Crestron processor (CIP server) for tests — of this library and of code using it.

It behaves like a real processor as far as observed: greets with "program ready", registers
panels, answers heartbeats, dumps only non-default joins on an update request and ends the dump
with end-of-query. Failure modes: wrong IP ID, silence (half-open TCP), dropped connection,
frames split across writes or coalesced into one write.

    async with FakeProcessor(ipids={0x03}) as proc:
        proc.set_analog(361, 65535)
        client = CipClient("127.0.0.1", 0x03, port=proc.port)
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from . import protocol as p


def digital_frame(join: int, value: bool) -> bytes:
    raw = join - 1
    return p.frame(p.FrameType.DATA, bytes([0, 0, 3, 0, raw & 0xFF, (raw >> 8) | (0 if value else 0x80)]))


def analog_frame(join: int, value: int) -> bytes:
    return p.frame(p.FrameType.DATA, b"\x00\x00\x05\x14" + (join - 1).to_bytes(2, "big") + value.to_bytes(2, "big"))


def packed_digital_frame(values: dict[int, bool]) -> bytes:
    """Several digitals in ONE sub-packet, the way a processor sends its initial dump."""
    body = b"".join(bytes([(j - 1) & 0xFF, ((j - 1) >> 8) | (0 if v else 0x80)]) for j, v in values.items())
    return p.frame(p.FrameType.DATA, b"\x00\x00" + bytes([len(body) + 1, p.Cresnet.DIGITAL]) + body)


def packed_analog_frame(values: dict[int, int]) -> bytes:
    body = b"".join((j - 1).to_bytes(2, "big") + v.to_bytes(2, "big") for j, v in values.items())
    return p.frame(p.FrameType.DATA, b"\x00\x00" + bytes([len(body) + 1, p.Cresnet.ANALOG]) + body)


def _chunks(values: dict, size: int) -> list[dict]:
    items = list(values.items())
    return [dict(items[i:i + size]) for i in range(0, len(items), size)]


def serial_frame(join: int, text: str, flags: int = p.SERIAL_COMPLETE, *, utf16: bool = False) -> bytes:
    data = text.encode("utf-16-le" if utf16 else "utf-8")
    flags |= p.SERIAL_UTF16 if utf16 else 0
    body = bytes([p.Cresnet.SERIAL]) + (join - 1).to_bytes(2, "big") + bytes([flags]) + data
    return p.frame(p.FrameType.EXTENDED_DATA, b"\x00\x00" + len(body).to_bytes(2, "big") + body)


class FakeProcessor:
    """Minimal CIP server on 127.0.0.1 with a join table and a record of what panels sent."""

    def __init__(self, ipids: set[int] | None = None, *, program_status: int = p.ProgramStatus.READY,
                 username: str | None = None, password: str | None = None, packed_dump: bool = True) -> None:
        self.ipids = ipids if ipids is not None else {0x03}
        self.program_status = program_status
        self.credentials = f"{username}:{password}" if username is not None else None
        self.packed_dump = packed_dump  # pack the initial dump like a real processor (up to 32 per sub-packet)
        self.digital: dict[int, bool] = {}
        self.analog: dict[int, int] = {}
        self.serial: dict[int, str] = {}
        self.received: list[p.Message] = []  # joins, commands, heartbeats from panels
        self.connections = 0
        self.mute = False          # ignore everything, send nothing (half-open connection)
        self.split_writes = False  # send every frame in two writes
        self.coalesce = False      # buffer frames and send them in one write
        self.on_join: Callable[[FakeProcessor, p.JoinUpdate], None] | None = None  # "the program"
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._pending = bytearray()
        self.port = 0

    async def __aenter__(self) -> FakeProcessor:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.drop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # --- test controls -------------------------------------------------------------

    def set_digital(self, join: int, value: bool) -> None:
        self.digital[join] = value
        self.send(digital_frame(join, value))

    def set_analog(self, join: int, value: int) -> None:
        self.analog[join] = value
        self.send(analog_frame(join, value))

    def set_serial(self, join: int, text: str) -> None:
        self.serial[join] = text
        self.send(serial_frame(join, text))

    def send(self, data: bytes) -> None:
        """Send raw bytes to all connected panels."""
        if self.mute:
            return
        if self.coalesce:
            self._pending += data
            return
        for writer in list(self._writers):
            if self.split_writes and len(data) > 1:
                mid = len(data) // 2
                writer.write(data[:mid])
                asyncio.get_running_loop().call_later(0.02, writer.write, data[mid:])
            else:
                writer.write(data)

    def flush(self) -> None:
        """Send frames buffered while ``coalesce`` was set, as one write."""
        data, self._pending = bytes(self._pending), bytearray()
        coalesce, self.coalesce = self.coalesce, False
        self.send(data)
        self.coalesce = coalesce

    def drop(self) -> None:
        """Close all panel connections (like a processor reboot)."""
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()

    def joins_received(self, join_type: p.JoinType | None = None) -> list[p.JoinUpdate]:
        return [m for m in self.received if isinstance(m, p.JoinUpdate)
                and (join_type is None or m.type == join_type)]

    # --- server --------------------------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        decoder, serial = p.FrameDecoder(), p.SerialAssembler()
        self._reply(writer, p.frame(p.FrameType.PROGRAM_STATUS, bytes([self.program_status])))
        try:
            while data := await reader.read(4096):
                if self.mute:
                    continue
                for frame_type, payload in decoder.feed(data):
                    self._handle(writer, serial, frame_type, payload)
        except (ConnectionError, OSError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _reply(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        if not self.mute and not writer.is_closing():
            writer.write(data)

    def _handle(self, writer: asyncio.StreamWriter, serial: p.SerialAssembler, frame_type: int, payload: bytes) -> None:
        match frame_type:
            case p.FrameType.CONNECT:
                ok = payload[5] in self.ipids
                self._reply(writer, p.frame(p.FrameType.CONNECT_RESPONSE,
                                            b"\x00\x00\x00\x1f" if ok else p.IPID_NOT_DEFINED))
                return
            case p.FrameType.AUTH:
                ok = payload[2:].rstrip(b"\x00").decode() == self.credentials
                self._reply(writer, p.frame(p.FrameType.AUTH_RESPONSE, b"\x00\x00" + bytes([1 if ok else 0])))
                return
            case p.FrameType.HEARTBEAT:
                self.received.append(p.Heartbeat(response=False))
                self._reply(writer, p.build_heartbeat(response=True))
                return
            case p.FrameType.HEARTBEAT_RESPONSE:
                self.received.append(p.Heartbeat(response=True))
                return
        for msg in p.decode_frame(frame_type, payload):
            if isinstance(msg, p.SerialChunk):
                msg = serial.add(msg)
                if msg is None:
                    continue
            self.received.append(msg)
            if isinstance(msg, p.CommandMessage) and msg.command == p.Command.UPDATE_REQUEST:
                self._dump(writer)
            elif isinstance(msg, p.JoinUpdate) and self.on_join:
                self.on_join(self, msg)

    def _dump(self, writer: asyncio.StreamWriter) -> None:
        frames = [p.build_command(p.Command.UPDATE_REQUEST)]
        digitals = {j: v for j, v in self.digital.items() if v}
        analogs = {j: v for j, v in self.analog.items() if v}
        if self.packed_dump:
            frames += [packed_digital_frame(c) for c in _chunks(digitals, 32)]
            frames += [packed_analog_frame(c) for c in _chunks(analogs, 32)]
        else:
            frames += [digital_frame(j, v) for j, v in digitals.items()]
            frames += [analog_frame(j, v) for j, v in analogs.items()]
        frames += [serial_frame(j, v) for j, v in self.serial.items() if v]
        frames += [p.build_command(p.Command.PENULTIMATE), p.build_command(p.Command.END_OF_QUERY)]
        for data in frames:
            self._reply(writer, data)
