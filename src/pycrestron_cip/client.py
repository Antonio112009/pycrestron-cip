"""asyncio client that connects to a Crestron processor as a panel (XPanel) over CIP."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import socket
import ssl as _ssl
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from . import protocol as p
from .exceptions import (
    AuthError,
    CipConnectionError,
    CipError,
    CipTimeoutError,
    IpidNotDefinedError,
    NotConnectedError,
)

_log = logging.getLogger(__name__)

DEFAULT_PORT = 41794
DEFAULT_TLS_PORT = 41796

JoinCallback = Callable[[p.JoinUpdate], None]
StateCallback = Callable[["ConnectionState"], None]
_Key = tuple[p.JoinType, int, int | None]  # (type, join, smart object)
_DEFAULTS: dict[p.JoinType, bool | int | str] = {p.JoinType.DIGITAL: False, p.JoinType.ANALOG: 0, p.JoinType.SERIAL: ""}


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    REGISTERING = "registering"
    SYNCING = "syncing"
    READY = "ready"
    CLOSED = "closed"


class CipClient:
    """A CIP panel connection with join caches, heartbeats and automatic reconnect.

    Typical use::

        async with CipClient("192.168.1.10", 0x03) as panel:
            panel.subscribe(print)
            await panel.pulse(101)
            print(panel.get_analog(361))

    Inputs (processor -> panel) are cached and pushed to subscribers. Outputs set with
    ``set_*`` are cached too and replayed after every (re)connect, so they survive a
    processor reboot. Button presses are never replayed.

    Callbacks run in the event loop thread and must not block.
    """

    def __init__(
        self,
        host: str,
        ipid: int,
        *,
        port: int | None = None,
        ssl: _ssl.SSLContext | bool | None = None,
        username: str | None = None,
        password: str | None = None,
        connect_timeout: float = 10.0,
        heartbeat_interval: float = 15.0,
        liveness_timeout: float = 35.0,
        reconnect: bool = True,
        backoff_min: float = 1.0,
        backoff_max: float = 60.0,
        button_repeat: float = 0.5,
    ) -> None:
        p.build_connect(ipid)  # validates the IP ID early
        self.host = host
        self.ipid = ipid
        self._ssl = _make_ssl(ssl)
        self.port = port or (DEFAULT_TLS_PORT if self._ssl else DEFAULT_PORT)
        self._auth = (username, password or "") if username is not None else None
        self.connect_timeout = connect_timeout
        self.heartbeat_interval = heartbeat_interval
        self.liveness_timeout = liveness_timeout
        self.reconnect = reconnect
        self.backoff_min = backoff_min
        self.backoff_max = backoff_max
        self.button_repeat = button_repeat

        self._state = ConnectionState.DISCONNECTED
        self._inputs: dict[_Key, bool | int | str] = {}
        self._outputs: dict[_Key, bool | int | str] = {}
        self._pressed: set[int] = set()
        self._join_subs: list[tuple[JoinCallback, p.JoinType | None, int | None]] = []
        self._state_subs: list[StateCallback] = []

        self._decoder = p.FrameDecoder()
        self._serial = p.SerialAssembler()
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._supervisor: asyncio.Task[None] | None = None
        self._lost = asyncio.Event()
        self._ready = asyncio.Event()
        self._waiters: dict[str, asyncio.Future[Any]] = {}
        self._closing = False
        self._dropping = False  # a teardown for a lost connection is already scheduled
        self._background: asyncio.Task[None] | None = None
        self.last_rx: float | None = None
        self.last_error: str | None = None
        self.stats = {
            "connects": 0,
            "disconnects": 0,
            "frames_rx": 0,
            "frames_tx": 0,
            "decode_errors": 0,
            "heartbeats_answered": 0,
        }

    # --- lifecycle ---------------------------------------------------------------

    async def __aenter__(self) -> CipClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Connect, register and wait for the initial sync.

        Raises on failure (:class:`IpidNotDefinedError`, :class:`AuthError`,
        :class:`CipTimeoutError`, :class:`CipConnectionError`). After a successful connect the
        client reconnects by itself when ``reconnect`` is set.
        """
        if self._closing:
            raise CipError("client is closed")
        await self._open()
        if self.reconnect and self._supervisor is None:
            self._supervisor = asyncio.create_task(self._supervise(), name="cip-supervisor")

    async def close(self) -> None:
        """Say goodbye to the processor and stop all tasks."""
        self._closing = True
        if self._supervisor:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
            self._supervisor = None
        if self._writer and not self._writer.is_closing():
            self._write(p.build_disconnect())
        await self._teardown(None)
        self._set_state(ConnectionState.CLOSED)

    async def wait_ready(self, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Wait until the connection is synced (useful after a reconnect)."""
        try:
            async with asyncio.timeout(timeout):
                await self._ready.wait()
        except TimeoutError as err:
            raise CipTimeoutError("not connected") from err

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def available(self) -> bool:
        return self._state is ConnectionState.READY

    # --- inputs ------------------------------------------------------------------

    def get_digital(self, join: int, smart_object: int | None = None) -> bool:
        return bool(self._inputs.get((p.JoinType.DIGITAL, join, smart_object), False))

    def get_analog(self, join: int, smart_object: int | None = None) -> int:
        return int(self._inputs.get((p.JoinType.ANALOG, join, smart_object), 0))

    def get_serial(self, join: int, smart_object: int | None = None) -> str:
        return str(self._inputs.get((p.JoinType.SERIAL, join, smart_object), ""))

    def has_input(self, join_type: p.JoinType, join: int, smart_object: int | None = None) -> bool:
        """True once the processor has sent this join during the current connection."""
        return (join_type, join, smart_object) in self._inputs

    def subscribe(
        self, callback: JoinCallback, join_type: p.JoinType | None = None, join: int | None = None
    ) -> Callable[[], None]:
        """Call ``callback(update)`` for incoming joins (optionally filtered). Returns unsubscribe."""
        entry = (callback, join_type, join)
        self._join_subs.append(entry)
        return lambda: self._join_subs.remove(entry) if entry in self._join_subs else None

    def subscribe_state(self, callback: StateCallback) -> Callable[[], None]:
        self._state_subs.append(callback)
        return lambda: self._state_subs.remove(callback) if callback in self._state_subs else None

    # --- outputs -----------------------------------------------------------------

    def set_digital(self, join: int, value: bool) -> None:
        """Latched digital output (kept and replayed after reconnect)."""
        self._set_output(p.JoinType.DIGITAL, join, bool(value), [p.build_digital(join, bool(value))])

    def set_analog(self, join: int, value: int) -> None:
        self._set_output(p.JoinType.ANALOG, join, value, [p.build_analog(join, value)])

    def set_serial(self, join: int, text: str) -> None:
        self._set_output(p.JoinType.SERIAL, join, text, p.build_serial(join, text))

    def press(self, join: int) -> None:
        """Hold a button (re-sent every ``button_repeat`` s until :meth:`release`)."""
        self._require_ready()
        self._pressed.add(join)
        self._write(p.build_digital(join, True, repeat=True))

    def release(self, join: int) -> None:
        self._pressed.discard(join)
        if self.available:
            self._write(p.build_digital(join, False, repeat=True))

    async def pulse(self, join: int, hold: float = 0.1) -> None:
        """Press and release a button, like a tap on the touch panel."""
        self.press(join)
        try:
            await asyncio.sleep(hold)
        finally:
            self.release(join)

    def request_update(self) -> None:
        """Ask the processor to send its join values again."""
        self._require_ready()
        self._write(p.build_command(p.Command.UPDATE_REQUEST))

    def snapshot(self) -> dict[str, Any]:
        """State for diagnostics (no credentials)."""
        return {
            "host": self.host,
            "port": self.port,
            "ipid": f"0x{self.ipid:02X}",
            "tls": bool(self._ssl),
            "state": self._state.value,
            "last_error": self.last_error,
            "seconds_since_rx": None if self.last_rx is None else round(time.monotonic() - self.last_rx, 1),
            "inputs": len(self._inputs),
            "outputs": len(self._outputs),
            "pressed": sorted(self._pressed),
            "stats": dict(self.stats),
        }

    # --- connection --------------------------------------------------------------

    async def _open(self) -> None:
        await self._teardown(None)
        self._set_state(ConnectionState.CONNECTING)
        loop = asyncio.get_running_loop()
        self._waiters = {k: loop.create_future() for k in ("program", "registered", "auth", "synced")}
        try:
            async with asyncio.timeout(self.connect_timeout):
                reader, writer = await asyncio.open_connection(self.host, self.port, ssl=self._ssl)
                self._writer = writer
                self._decoder.reset()
                self._serial.reset()
                sock = writer.get_extra_info("socket")
                if sock is not None:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self.last_rx = time.monotonic()
                self._lost.clear()
                self._spawn(self._read_loop(reader), "cip-reader")

                await self._waiters["program"]
                self._set_state(ConnectionState.REGISTERING)
                self._write(p.build_connect(self.ipid))
                if not await self._waiters["registered"]:
                    raise IpidNotDefinedError(f"IP ID 0x{self.ipid:02X} is not defined on {self.host}")
                if self._auth:
                    self._write(p.build_auth(*self._auth))
                    if not await self._waiters["auth"]:
                        raise AuthError("processor rejected the credentials")

                self._set_state(ConnectionState.SYNCING)
                self._inputs.clear()  # the dump only contains non-default values
                self._write(p.build_command(p.Command.UPDATE_REQUEST))
                await self._waiters["synced"]
        except TimeoutError as err:
            await self._fail(CipTimeoutError(f"no answer from {self.host}:{self.port} while {self._state.value}"), err)
        except OSError as err:
            await self._fail(CipConnectionError(f"cannot connect to {self.host}:{self.port}: {err}"), err)
        except CipError as err:
            await self._fail(err, None)

        self.stats["connects"] += 1
        self.last_error = None
        self._spawn(self._heartbeat_loop(), "cip-heartbeat")
        self._spawn(self._button_loop(), "cip-buttons")
        self._set_state(ConnectionState.READY)
        self._ready.set()
        _log.info("Connected to %s:%s as IP ID 0x%02X", self.host, self.port, self.ipid)

    async def _fail(self, err: CipError, cause: BaseException | None) -> None:
        self.last_error = str(err)
        await self._teardown(err)
        raise err from cause

    async def _supervise(self) -> None:
        delay = self.backoff_min
        while not self._closing:
            await self._lost.wait()
            if self._closing:
                return
            await asyncio.sleep(delay * random.uniform(0.8, 1.2))
            try:
                await self._open()
            except IpidNotDefinedError as err:
                _log.warning("%s; retrying in %.0f s", err, self.backoff_max)
                delay = self.backoff_max
            except CipError as err:
                _log.info("Reconnect failed: %s", err)
                delay = min(delay * 2, self.backoff_max)
            else:
                delay = self.backoff_min

    async def _teardown(self, err: BaseException | None) -> None:
        """Drop the current connection (if any); the supervisor decides what happens next."""
        was_up = self._writer is not None
        self._ready.clear()
        current = asyncio.current_task()
        tasks = [t for t in self._tasks if t is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for fut in self._waiters.values():
            if not fut.done():
                fut.set_exception(err or CipConnectionError("connection closed"))
            if fut.done() and not fut.cancelled():
                fut.exception()  # mark as retrieved
        if self._writer is not None:
            writer, self._writer = self._writer, None
            writer.close()
            with contextlib.suppress(OSError, TimeoutError, _ssl.SSLError):
                await asyncio.wait_for(writer.wait_closed(), 1)
        if was_up:
            self.stats["disconnects"] += 1
        if not self._closing:
            self._set_state(ConnectionState.DISCONNECTED)
        self._dropping = False
        self._lost.set()

    def _connection_lost(self, err: CipError) -> None:
        if self._writer is None or self._dropping:
            return
        self._dropping = True
        self.last_error = str(err)
        _log.warning("Connection to %s lost: %s", self.host, err)
        self._spawn(self._teardown(err), "cip-teardown", track=False)

    def _spawn(self, coro: Any, name: str, *, track: bool = True) -> None:
        task = asyncio.create_task(coro, name=name)
        if track:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:  # keep a reference until done
            self._background = task

    # --- loops -------------------------------------------------------------------

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while data := await reader.read(4096):
                self.last_rx = time.monotonic()
                for frame_type, payload in self._decoder.feed(data):
                    self.stats["frames_rx"] += 1
                    _log.debug("< %02x %s", frame_type, payload.hex())
                    try:
                        messages = p.decode_frame(frame_type, payload)
                    except p.ProtocolError as err:
                        self.stats["decode_errors"] += 1
                        _log.warning("Bad frame: %s", err)
                        continue
                    for msg in messages:
                        self._handle(msg)
            self._connection_lost(CipConnectionError("processor closed the connection"))
        except OSError as err:
            self._connection_lost(CipConnectionError(str(err)))

    async def _heartbeat_loop(self) -> None:
        tick = min(self.heartbeat_interval, self.liveness_timeout / 3)
        last_sent = time.monotonic()
        while True:
            await asyncio.sleep(tick)
            now = time.monotonic()
            if self.last_rx is not None and now - self.last_rx > self.liveness_timeout:
                self._connection_lost(CipTimeoutError(f"nothing received for {self.liveness_timeout:.0f} s"))
                return
            if now - last_sent >= self.heartbeat_interval:
                self._write(p.build_heartbeat())
                last_sent = now

    async def _button_loop(self) -> None:
        while True:
            await asyncio.sleep(self.button_repeat)
            for join in list(self._pressed):
                self._write(p.build_digital(join, True, repeat=True))

    # --- incoming ----------------------------------------------------------------

    def _handle(self, msg: p.Message) -> None:
        match msg:
            case p.JoinUpdate():
                self._store(msg)
            case p.SerialChunk():
                if (update := self._serial.add(msg)) is not None:
                    self._store(update)
            case p.Heartbeat(response=False):
                self.stats["heartbeats_answered"] += 1
                self._write(p.build_heartbeat(response=True))
            case p.CommandMessage(command=p.Command.END_OF_QUERY):
                self._write(p.build_command(p.Command.END_OF_QUERY_ACK))
                self._write(p.build_heartbeat())
                for (kind, join, _), value in self._outputs.items():
                    self._write_output(kind, join, value)
                _resolve(self._waiters.get("synced"), True)
            case p.CommandMessage(command=command, data=data) if command not in p.Command:
                _log.debug("Unhandled command 0x%02x %s", command, data.hex())
            case p.ProgramStatusMessage(status=p.ProgramStatus.READY):
                _resolve(self._waiters.get("program"), True)
            case p.ProgramStatusMessage(status=status):
                _log.info("Processor program not running (status %s)", status)
            case p.ConnectResponse(accepted=accepted):
                _resolve(self._waiters.get("registered"), accepted)
            case p.AuthResponse(access_level=level):
                _resolve(self._waiters.get("auth"), level > 0)
            case p.Disconnect():
                self._connection_lost(CipConnectionError("processor ended the session"))

    def _store(self, update: p.JoinUpdate) -> None:
        self._inputs[(update.type, update.join, update.smart_object)] = update.value
        for callback, join_type, join in list(self._join_subs):
            if (join_type is None or join_type == update.type) and (join is None or join == update.join):
                try:
                    callback(update)
                except Exception:  # a broken subscriber must not kill the connection
                    _log.exception("Join callback failed")

    # --- outgoing ----------------------------------------------------------------

    def _set_output(self, kind: p.JoinType, join: int, value: bool | int | str, frames: list[bytes]) -> None:
        if value == _DEFAULTS[kind]:
            self._outputs.pop((kind, join, None), None)
        else:
            self._outputs[(kind, join, None)] = value
        if self.available:
            for data in frames:
                self._write(data)

    def _write_output(self, kind: p.JoinType, join: int, value: bool | int | str) -> None:
        match kind:
            case p.JoinType.DIGITAL:
                self._write(p.build_digital(join, bool(value)))
            case p.JoinType.ANALOG:
                self._write(p.build_analog(join, int(value)))
            case p.JoinType.SERIAL:
                for data in p.build_serial(join, str(value)):
                    self._write(data)

    def _require_ready(self) -> None:
        if not self.available:
            raise NotConnectedError(f"not connected to {self.host} ({self._state.value})")

    def _write(self, data: bytes) -> None:
        if self._writer is None or self._writer.is_closing():
            return
        _log.debug("> %s", data.hex())
        self.stats["frames_tx"] += 1
        self._writer.write(data)

    def _set_state(self, state: ConnectionState) -> None:
        if state is self._state:
            return
        self._state = state
        for callback in list(self._state_subs):
            try:
                callback(state)
            except Exception:
                _log.exception("State callback failed")


def _resolve(fut: asyncio.Future[Any] | None, value: Any) -> None:
    if fut is not None and not fut.done():
        fut.set_result(value)


def _make_ssl(value: _ssl.SSLContext | bool | None) -> _ssl.SSLContext | None:
    """``True`` = TLS without certificate checks (processors use self-signed certificates)."""
    if isinstance(value, _ssl.SSLContext):
        return value
    if not value:
        return None
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    return ctx
