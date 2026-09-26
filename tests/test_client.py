"""CipClient against the fake processor."""
import asyncio

import pytest

from pycrestron_cip import (
    AuthError,
    CipClient,
    CipConnectionError,
    CipTimeoutError,
    ConnectionState,
    IpidNotDefinedError,
    JoinType,
    JoinUpdate,
    NotConnectedError,
)
from pycrestron_cip import protocol as p
from pycrestron_cip.testing import FakeProcessor

FAST = {"connect_timeout": 2, "heartbeat_interval": 0.2, "liveness_timeout": 0.8,
        "backoff_min": 0.05, "backoff_max": 0.2, "button_repeat": 0.1}


async def until(cond, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


@pytest.fixture
async def proc():
    async with FakeProcessor(ipids={0x03}) as fake:
        yield fake


@pytest.fixture
async def client(proc):
    c = CipClient("127.0.0.1", 0x03, port=proc.port, **FAST)
    await c.connect()
    yield c
    await c.close()


async def test_connect_sync_and_inputs(proc):
    proc.digital[350] = True
    proc.analog[361] = 65535
    proc.serial[100] = "Living Area"
    states = []
    c = CipClient("127.0.0.1", 0x03, port=proc.port, **FAST)
    c.subscribe_state(states.append)
    await c.connect()
    try:
        assert c.available and c.state is ConnectionState.READY
        assert c.get_digital(350) and c.get_analog(361) == 65535 and c.get_serial(100) == "Living Area"
        assert not c.get_digital(351) and not c.has_input(JoinType.DIGITAL, 351)
        assert states == [ConnectionState.CONNECTING, ConnectionState.REGISTERING,
                          ConnectionState.SYNCING, ConnectionState.READY]
        await until(lambda: p.CommandMessage(p.Command.END_OF_QUERY_ACK) in proc.received)
    finally:
        await c.close()
    assert c.state is ConnectionState.CLOSED


async def test_events_and_subscriptions(proc, client):
    everything, analogs, one = [], [], []
    client.subscribe(everything.append)
    client.subscribe(analogs.append, JoinType.ANALOG)
    unsub = client.subscribe(one.append, JoinType.DIGITAL, 101)
    proc.set_digital(101, True)
    proc.set_analog(361, 100)
    proc.set_serial(100, "Кухня")
    await until(lambda: len(everything) == 3)
    assert everything[2] == JoinUpdate(JoinType.SERIAL, 100, "Кухня")
    assert analogs == [JoinUpdate(JoinType.ANALOG, 361, 100)]
    assert one == [JoinUpdate(JoinType.DIGITAL, 101, True)]
    unsub()
    proc.set_digital(101, False)
    await until(lambda: len(everything) == 4)
    assert len(one) == 1


async def test_broken_callback_does_not_kill_connection(proc, client):
    client.subscribe(lambda u: 1 / 0)
    proc.set_digital(1, True)
    proc.set_digital(2, True)
    await until(lambda: client.get_digital(2))
    assert client.available


async def test_outputs(proc, client):
    client.set_digital(5, True)
    client.set_analog(6, 1234)
    client.set_serial(7, "hello")
    await until(lambda: len(proc.joins_received()) == 3)
    assert proc.joins_received() == [JoinUpdate(JoinType.DIGITAL, 5, True), JoinUpdate(JoinType.ANALOG, 6, 1234),
                                     JoinUpdate(JoinType.SERIAL, 7, "hello")]


async def test_pulse_and_held_button_repeats(proc, client):
    await client.pulse(101, hold=0.05)
    await until(lambda: JoinUpdate(JoinType.DIGITAL, 101, False) in proc.joins_received())
    client.press(102)
    await asyncio.sleep(0.35)
    client.release(102)
    presses = [m for m in proc.joins_received() if m.join == 102 and m.value]
    assert len(presses) >= 3  # initial press + repeats every 0.1 s
    await until(lambda: proc.joins_received()[-1] == JoinUpdate(JoinType.DIGITAL, 102, False))


async def test_frames_split_and_coalesced(proc, client):
    proc.split_writes = True
    proc.set_analog(361, 777)
    await until(lambda: client.get_analog(361) == 777)
    proc.split_writes, proc.coalesce = False, True
    for j in range(1, 21):
        proc.set_digital(j, True)
    proc.flush()
    await until(lambda: all(client.get_digital(j) for j in range(1, 21)))


async def test_answers_processor_heartbeat(proc, client):
    proc.send(p.build_heartbeat())
    await until(lambda: client.stats["heartbeats_answered"] == 1)
    await until(lambda: p.Heartbeat(response=True) in proc.received)


async def test_sends_heartbeats(proc, client):
    await asyncio.sleep(0.5)
    assert proc.received.count(p.Heartbeat(response=False)) >= 2


async def test_reconnect_after_drop_replays_outputs(proc, client):
    client.set_analog(20, 42)
    client.set_digital(21, True)
    await until(lambda: len(proc.joins_received()) == 2)
    proc.received.clear()
    proc.digital[30] = True
    proc.drop()
    await until(lambda: client.state is not ConnectionState.READY)
    with pytest.raises(NotConnectedError):
        client.press(1)
    await client.wait_ready(3)
    assert proc.connections == 2 and client.get_digital(30)
    await until(lambda: len(proc.joins_received()) == 2)
    assert set(proc.joins_received()) == {JoinUpdate(JoinType.ANALOG, 20, 42), JoinUpdate(JoinType.DIGITAL, 21, True)}
    assert client.stats["disconnects"] == 1


async def test_silent_processor_detected_and_reconnected(proc, client):
    proc.mute = True
    await until(lambda: not client.available, timeout=3)
    assert "nothing received" in client.last_error
    proc.mute = False
    proc.drop()  # the old half-open socket is gone; the new one works
    await client.wait_ready(5)


async def test_processor_disconnect_frame(proc, client):
    proc.send(p.frame(p.FrameType.DISCONNECT, b""))
    await until(lambda: not client.available)
    await client.wait_ready(3)
    assert proc.connections == 2


async def test_ipid_not_defined():
    async with FakeProcessor(ipids={0x04}) as proc:
        c = CipClient("127.0.0.1", 0x03, port=proc.port, **FAST)
        with pytest.raises(IpidNotDefinedError):
            await c.connect()
        assert c.state is ConnectionState.DISCONNECTED
        await c.close()


async def test_program_not_running_times_out():
    async with FakeProcessor(program_status=p.ProgramStatus.NOT_RUNNING) as proc:
        c = CipClient("127.0.0.1", 0x03, port=proc.port, **{**FAST, "connect_timeout": 0.5})
        with pytest.raises(CipTimeoutError):
            await c.connect()
        await c.close()


async def test_unreachable():
    c = CipClient("127.0.0.1", 0x03, port=1, **FAST)
    with pytest.raises(CipConnectionError):
        await c.connect()
    await c.close()


async def test_auth():
    async with FakeProcessor(username="admin", password="secret") as proc:
        bad = CipClient("127.0.0.1", 0x03, port=proc.port, username="admin", password="nope", **FAST)
        with pytest.raises(AuthError):
            await bad.connect()
        await bad.close()
        good = CipClient("127.0.0.1", 0x03, port=proc.port, username="admin", password="secret", **FAST)
        await good.connect()
        assert good.available
        assert "secret" not in str(good.snapshot())
        await good.close()


async def test_processor_program_reacts(proc, client):
    """on_join lets a test script the processor, e.g. a scene button lighting its indicator."""
    def program(fake, update):
        if update.type is JoinType.DIGITAL and update.join == 351 and update.value:
            fake.set_digital(351, True)
            fake.set_analog(361, 65535)
    proc.on_join = program
    await client.pulse(351)
    await until(lambda: client.get_analog(361) == 65535 and client.get_digital(351))
