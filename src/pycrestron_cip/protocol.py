"""Sans-I/O codec for the Crestron CIP protocol (TCP 41794 / TLS 41796).

Nothing here touches sockets: :class:`FrameDecoder` turns a byte stream into frames,
:func:`decode_frame` turns frames into typed messages, and the ``build_*`` functions produce
the bytes a panel (XPanel) sends. The transport lives in :mod:`pycrestron_cip.client`.

Wire format: every frame is ``[type:1][length:2 BE][payload]``. Most payloads start with a
2-byte handle (always ``00 00`` on TCP so far). Data frames (0x05) carry one or more Cresnet
sub-packets ``[len:1][type:1][data]``; extended frames (0x12) carry ``[len:2][type:1][data]``.
Joins are 1-based in the API and 0-based on the wire.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum, StrEnum


class FrameType(IntEnum):
    CONNECT = 0x01
    CONNECT_RESPONSE = 0x02
    DISCONNECT = 0x03
    DISCONNECT_RESPONSE = 0x04
    DATA = 0x05
    CONNECT_ALT = 0x0A
    AUTH = 0x0B
    AUTH_RESPONSE = 0x0C
    HEARTBEAT = 0x0D
    HEARTBEAT_RESPONSE = 0x0E
    PROGRAM_STATUS = 0x0F
    EXTENDED_DATA = 0x12


class Cresnet(IntEnum):
    """Sub-packet types inside DATA / EXTENDED_DATA frames."""

    DIGITAL = 0x00
    ANALOG_LEGACY = 0x01
    COMMAND = 0x03
    DATE_TIME = 0x08
    ANALOG = 0x14
    SERIAL_SHORT = 0x15
    DIGITAL_REPEAT = 0x27
    SERIAL = 0x34
    SMART_OBJECT = 0x38
    SMART_OBJECT_EXTENDED = 0x39


class Command(IntEnum):
    UPDATE_REQUEST = 0x00
    PENULTIMATE = 0x16
    END_OF_QUERY = 0x1C
    END_OF_QUERY_ACK = 0x1D


class ProgramStatus(IntEnum):
    LOADING = 0
    NOT_RUNNING = 1
    READY = 2


class JoinType(StrEnum):
    DIGITAL = "d"
    ANALOG = "a"
    SERIAL = "s"


# serial flags
SERIAL_START = 0x01
SERIAL_END = 0x02
SERIAL_UTF16 = 0x04
SERIAL_COMPLETE = SERIAL_START | SERIAL_END

MAX_JOIN = 0x8000  # digital joins are 15 bit on the wire
MAX_ANALOG = 0xFFFF
SERIAL_CHUNK = 240  # bytes of text per extended-serial frame when sending

IPID_NOT_DEFINED = b"\xff\xff\x02"


class ProtocolError(ValueError):
    """A frame could not be decoded."""


# --- messages ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JoinUpdate:
    """A join value sent by the processor. ``smart_object`` is None for plain joins."""

    type: JoinType
    join: int
    value: bool | int | str
    smart_object: int | None = None


@dataclass(frozen=True, slots=True)
class SerialChunk:
    """Part of a serial value; :class:`SerialAssembler` joins chunks into a JoinUpdate."""

    join: int
    flags: int
    data: bytes
    smart_object: int | None = None


@dataclass(frozen=True, slots=True)
class CommandMessage:
    command: int


@dataclass(frozen=True, slots=True)
class DateTimeMessage:
    raw: bytes


@dataclass(frozen=True, slots=True)
class ProgramStatusMessage:
    status: int


@dataclass(frozen=True, slots=True)
class ConnectResponse:
    """Registration result; ``accepted`` is False when the IP ID is not defined."""

    accepted: bool
    raw: bytes


@dataclass(frozen=True, slots=True)
class AuthResponse:
    access_level: int


@dataclass(frozen=True, slots=True)
class Heartbeat:
    response: bool


@dataclass(frozen=True, slots=True)
class Disconnect:
    pass


@dataclass(frozen=True, slots=True)
class Unknown:
    frame_type: int
    payload: bytes


Message = (JoinUpdate | SerialChunk | CommandMessage | DateTimeMessage | ProgramStatusMessage
           | ConnectResponse | AuthResponse | Heartbeat | Disconnect | Unknown)


# --- framing ----------------------------------------------------------------------


class FrameDecoder:
    """Incremental framer: feed arbitrary chunks, get complete ``(type, payload)`` frames.

    Frames split across reads are kept until complete; several frames in one read are all
    returned (the processor does both).
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf += data
        frames = []
        while len(self._buf) >= 3:
            length = int.from_bytes(self._buf[1:3], "big")
            if len(self._buf) < 3 + length:
                break
            frames.append((self._buf[0], bytes(self._buf[3:3 + length])))
            del self._buf[:3 + length]
        return frames

    def reset(self) -> None:
        self._buf.clear()

    @property
    def pending(self) -> int:
        """Bytes of an incomplete frame waiting for more data."""
        return len(self._buf)


def frame(frame_type: int, payload: bytes) -> bytes:
    if len(payload) > 0xFFFF:
        raise ValueError("CIP payload too long")
    return bytes([frame_type]) + len(payload).to_bytes(2, "big") + payload


# --- decoding ---------------------------------------------------------------------


def decode_frame(frame_type: int, payload: bytes) -> list[Message]:
    """Decode one frame into messages. Unknown sub-packets are skipped, not fatal."""
    try:
        match frame_type:
            case FrameType.DATA:
                return list(_decode_cresnet(payload[2:], wide=False))
            case FrameType.EXTENDED_DATA:
                return list(_decode_cresnet(payload[2:], wide=True))
            case FrameType.HEARTBEAT:
                return [Heartbeat(response=False)]
            case FrameType.HEARTBEAT_RESPONSE:
                return [Heartbeat(response=True)]
            case FrameType.PROGRAM_STATUS:
                return [ProgramStatusMessage(payload[0] if payload else ProgramStatus.READY)]
            case FrameType.CONNECT_RESPONSE:
                return [ConnectResponse(accepted=payload != IPID_NOT_DEFINED, raw=payload)]
            case FrameType.AUTH_RESPONSE:
                return [AuthResponse(payload[-1] if len(payload) > 2 else 0)]
            case FrameType.DISCONNECT | FrameType.DISCONNECT_RESPONSE:
                return [Disconnect()]
    except IndexError as err:
        raise ProtocolError(f"truncated frame 0x{frame_type:02x} {payload.hex()}") from err
    return [Unknown(frame_type, payload)]


def _decode_cresnet(data: bytes, *, wide: bool, smart_object: int | None = None
                    ) -> Iterator[Message]:
    """Walk ``[len][type][body]`` sub-packets (``len`` is 2 bytes when ``wide``)."""
    i, hdr = 0, 2 if wide else 1
    while i + hdr < len(data):
        length = int.from_bytes(data[i:i + hdr], "big")
        if length == 0:
            break
        start, end = i + hdr, i + hdr + length
        if end > len(data):
            raise ProtocolError(f"sub-packet overruns frame: {data.hex()}")
        yield from _decode_sub(data[start], data[start + 1:end], wide, smart_object)
        i = end


def _decode_sub(kind: int, body: bytes, wide: bool, so: int | None) -> Iterator[Message]:
    match kind:
        # The processor packs several joins into one digital/analog sub-packet (seen in the
        # initial dump on a 3-Series), so walk every pair, not just the first.
        case Cresnet.DIGITAL | Cresnet.DIGITAL_REPEAT:
            for i in range(0, len(body) - 1, 2):
                raw = body[i] | ((body[i + 1] & 0x7F) << 8)
                yield JoinUpdate(JoinType.DIGITAL, raw + 1, not body[i + 1] & 0x80, so)
        case Cresnet.ANALOG:
            for i in range(0, len(body) - 3, 4):
                yield JoinUpdate(JoinType.ANALOG, int.from_bytes(body[i:i + 2], "big") + 1,
                                 int.from_bytes(body[i + 2:i + 4], "big"), so)
        case Cresnet.ANALOG_LEGACY:
            if len(body) == 3:  # 1-byte join
                yield JoinUpdate(JoinType.ANALOG, body[0] + 1, int.from_bytes(body[1:3], "big"), so)
            else:
                yield JoinUpdate(JoinType.ANALOG, int.from_bytes(body[0:2], "big") + 1,
                                 int.from_bytes(body[2:4], "big"), so)
        case Cresnet.SERIAL | Cresnet.SERIAL_SHORT:
            yield SerialChunk(int.from_bytes(body[0:2], "big") + 1, body[2], body[3:], so)
        case Cresnet.COMMAND:
            yield CommandMessage(body[0])
        case Cresnet.DATE_TIME:
            yield DateTimeMessage(body)
        case Cresnet.SMART_OBJECT | Cresnet.SMART_OBJECT_EXTENDED:
            # [00 00 00][object id][nested sub-packets]
            yield from _decode_cresnet(body[4:], wide=wide, smart_object=body[3])
        case _:
            pass


def decode_text(data: bytes, flags: int) -> str:
    if flags & SERIAL_UTF16:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class SerialAssembler:
    """Joins serial chunks (start/end flags) into complete values."""

    def __init__(self) -> None:
        self._parts: dict[tuple[int | None, int], tuple[int, bytearray]] = {}

    def add(self, chunk: SerialChunk) -> JoinUpdate | None:
        key = (chunk.smart_object, chunk.join)
        if chunk.flags & SERIAL_START:
            self._parts[key] = (chunk.flags, bytearray(chunk.data))
        elif key in self._parts:
            self._parts[key][1].extend(chunk.data)
        else:  # no start flag and nothing pending: the chunk is the whole value
            return JoinUpdate(JoinType.SERIAL, chunk.join, decode_text(chunk.data, chunk.flags),
                              chunk.smart_object)
        if chunk.flags & SERIAL_END:
            flags, buf = self._parts.pop(key)
            return JoinUpdate(JoinType.SERIAL, chunk.join,
                              decode_text(bytes(buf), flags | chunk.flags), chunk.smart_object)
        return None

    def reset(self) -> None:
        self._parts.clear()


# --- building (panel -> processor) --------------------------------------------------


def _check_join(join: int) -> int:
    if not 1 <= join <= MAX_JOIN:
        raise ValueError(f"join out of range: {join}")
    return join - 1


def build_connect(ipid: int) -> bytes:
    """Register as panel ``ipid`` (modern form: extended serial, unicode, heartbeat)."""
    if not 0x03 <= ipid <= 0xFE:
        raise ValueError(f"IP ID out of range: 0x{ipid:02x}")
    return frame(FrameType.CONNECT, b"\x00\x00\x00\x00\x00" + bytes([ipid]) + b"\x40\xff\xff\xf1\x01")


def build_auth(username: str, password: str) -> bytes:
    return frame(FrameType.AUTH, b"\x00\x00" + f"{username}:{password}".encode() + b"\x00\x00\x00")


def build_command(command: int) -> bytes:
    return frame(FrameType.DATA, b"\x00\x00\x02\x03" + bytes([command]))


def build_heartbeat(response: bool = False) -> bytes:
    return frame(FrameType.HEARTBEAT_RESPONSE if response else FrameType.HEARTBEAT, b"\x00\x00")


def build_disconnect() -> bytes:
    return frame(FrameType.DISCONNECT, b"")


def build_digital(join: int, value: bool, *, repeat: bool = False) -> bytes:
    """Digital join; ``repeat`` = button style (0x27), which the processor auto-releases
    unless it is re-sent at least every 0.5 s."""
    raw = _check_join(join)
    kind = Cresnet.DIGITAL_REPEAT if repeat else Cresnet.DIGITAL
    hi = (raw >> 8) | (0 if value else 0x80)
    return frame(FrameType.DATA, bytes([0, 0, 3, kind, raw & 0xFF, hi]))


def build_analog(join: int, value: int) -> bytes:
    if not 0 <= value <= MAX_ANALOG:
        raise ValueError(f"analog value out of range: {value}")
    return frame(FrameType.DATA, b"\x00\x00\x05\x14" + _check_join(join).to_bytes(2, "big")
                 + value.to_bytes(2, "big"))


def build_serial(join: int, text: str) -> list[bytes]:
    """Extended serial frames for ``text`` (ASCII/UTF-8 as-is, otherwise UTF-16LE), chunked."""
    raw = _check_join(join)
    if text.isascii():
        data, extra = text.encode("ascii"), 0
    else:
        data, extra = text.encode("utf-16-le"), SERIAL_UTF16
    chunks = [data[i:i + SERIAL_CHUNK] for i in range(0, len(data), SERIAL_CHUNK)] or [b""]
    frames = []
    for n, chunk in enumerate(chunks):
        flags = extra | (SERIAL_START if n == 0 else 0) | (SERIAL_END if n == len(chunks) - 1 else 0)
        body = bytes([Cresnet.SERIAL]) + raw.to_bytes(2, "big") + bytes([flags]) + chunk
        frames.append(frame(FrameType.EXTENDED_DATA, b"\x00\x00" + len(body).to_bytes(2, "big") + body))
    return frames
