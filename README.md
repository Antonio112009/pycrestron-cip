# pycrestron-cip

[![CI](https://github.com/Antonio112009/pycrestron-cip/actions/workflows/ci.yml/badge.svg)](https://github.com/Antonio112009/pycrestron-cip/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pycrestron-cip?color=blue&label=pypi)](https://pypi.org/project/pycrestron-cip/)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/github/v/release/Antonio112009/pycrestron-cip?display_name=tag&sort=semver&color=orange&label=version)](https://github.com/Antonio112009/pycrestron-cip/releases/latest)

`pycrestron-cip` is a typed `asyncio` client for **CIP** (Crestron-over-IP), the protocol that touch panels and
XPanel use to talk to a Crestron control processor. Your code connects as a panel IP ID, sees what that panel
would see (digital, analog and serial joins) and can press its buttons.

It needs nothing from the processor's program: no SIMPL changes, no extra modules. If a panel IP ID is defined,
you can connect to it. That makes it a good base for Home Assistant integrations and other home automation.

> **Status: alpha.** Tested against a Crestron 3-Series processor. The API may still change.
>
> Not affiliated with, endorsed by or supported by Crestron Electronics, Inc.
> "Crestron" is a trademark of Crestron Electronics, Inc.

## Features

- Pure `asyncio`, no runtime dependencies, fully typed (`py.typed`)
- Registration, update request / end-of-query sync, heartbeats in both directions
- Correct TCP framing: frames split across reads, several frames per read, **several joins per sub-packet**
  (processors pack their initial dump; older clients lose most of it)
- Liveness timeout: a silent, half-open connection is noticed and reconnected
- Automatic reconnect with backoff, and a long retry interval for an undefined IP ID
- Input caches with callbacks; latched outputs are replayed after every reconnect
- Buttons: `press` / `release` with auto-repeat, and `pulse`
- Serial joins in UTF-8 and UTF-16, chunked long strings, short and extended forms, smart objects
- Optional TLS (port 41796) and username/password authentication
- `FakeProcessor`: a CIP server for your own tests, built from real captures
- `examples/probe.py`: a read-only tool that records everything a processor sends

## Requirements

- Python 3.13 or newer
- A Crestron processor reachable on TCP 41794 (or 41796 for TLS)
- A panel IP ID defined in the processor's program that nothing else is using (see [below](#things-to-know-about-processors))

## Installation

```bash
pip install pycrestron-cip
```

For local development:

```bash
git clone https://github.com/Antonio112009/pycrestron-cip.git
cd pycrestron-cip
pip install -e ".[dev]"
```

## Quick Start

```python
import asyncio

from pycrestron_cip import CipClient, JoinUpdate


def on_join(update: JoinUpdate) -> None:
    print(f"{update.type}{update.join} = {update.value!r}")


async def main() -> None:
    async with CipClient("192.168.1.10", 0x03) as panel:
        panel.subscribe(on_join)  # everything the processor sends
        await panel.pulse(101)  # tap button d101
        print(panel.get_analog(361))  # last value of a361
        panel.set_serial(10, "hello")  # latched serial output
        await asyncio.sleep(30)


asyncio.run(main())
```

## API

| Call | What it does |
|---|---|
| `await connect()` / `async with CipClient(...)` | Connect, register, wait for the initial sync. Raises `IpidNotDefinedError`, `AuthError`, `CipTimeoutError` or `CipConnectionError` |
| `await close()` | Say goodbye to the processor and stop all tasks |
| `get_digital/analog/serial(join)` | Last value the processor sent (the default when never sent) |
| `has_input(join_type, join)` | Whether the processor has sent this join at all |
| `subscribe(cb, join_type=None, join=None)` | Callback for incoming joins; returns an unsubscribe function |
| `subscribe_state(cb)` | Callback for connection state changes (`ConnectionState`) |
| `set_digital/analog/serial(join, value)` | Latched outputs, replayed after every reconnect |
| `press(join)` / `release(join)` / `await pulse(join)` | Buttons (held buttons auto-repeat) |
| `request_update()` | Ask the processor to send its values again |
| `available`, `state`, `await wait_ready()` | Connection status |
| `snapshot()`, `stats`, `last_error` | Diagnostics (no credentials) |

Smart-object joins: pass `smart_object=` to the getters; `JoinUpdate.smart_object` tells you where an update
came from.

### Options

| Option | Default | Meaning |
|---|---|---|
| `port` | 41794 (41796 with TLS) | TCP port |
| `ssl` | `None` | `True` for TLS without certificate checks, or an `ssl.SSLContext` |
| `username`, `password` | `None` | Credentials, if the processor requires authentication |
| `connect_timeout` | 10 s | Connect, register and sync must finish within this time |
| `heartbeat_interval` | 15 s | How often we send a heartbeat (processors answer, they do not ask) |
| `liveness_timeout` | 35 s | Nothing received for this long means the connection is dead |
| `reconnect` | `True` | Reconnect by itself after a successful first connect |
| `backoff_min`, `backoff_max` | 1 s, 60 s | Reconnect backoff |
| `button_repeat` | 0.5 s | Repeat interval for held buttons |

## Things to know about processors

- The processor sends a panel only **changes** relative to what it believes the panel already shows. After a
  (re)connect, the initial dump contains non-default values only, and a "paged" panel may not resend a value
  until it changes.
- Button-style digitals are released by the processor unless repeated within about 0.5 s. `press()` repeats
  for you until `release()`.
- **Two clients on one IP ID share one panel.** On paged panels that means one shared page: one client's
  navigation changes what the other sees. Give automation its own IP ID where you can.
- Directly setting an analog that the program drives (for example a dimmer level) often does nothing: the
  program expects the panel's raise/lower buttons.

## Testing Your Code

`FakeProcessor` is a small CIP server that behaves like a real processor as far as observed: it greets, registers
panels, answers heartbeats, packs its initial dump and ends it with end-of-query. It can also misbehave: wrong
IP ID, silence, dropped connections, split or coalesced frames.

```python
from pycrestron_cip import CipClient
from pycrestron_cip.testing import FakeProcessor


async def test_scene_indicator():
    async with FakeProcessor(ipids={0x03}) as proc:
        proc.digital[350] = True
        async with CipClient("127.0.0.1", 0x03, port=proc.port) as panel:
            assert panel.get_digital(350)
            await panel.pulse(101)
        assert [u.join for u in proc.joins_received()] == [101, 101]  # press, release
```

## Probing a Processor

`examples/probe.py` connects as a panel, records everything the processor sends and disconnects. It sends only
what the protocol requires (no button presses, no join values):

```bash
python examples/probe.py 192.168.1.10 0x03 --seconds 120 --log probe.log --json probe.json
```

Stop anything else that uses the same IP ID first. Please do not publish probe logs from a real installation:
they contain room names, addresses and whatever the panel shows.

## Protocol

[docs/protocol.md](docs/protocol.md) describes the wire format as implemented, what was verified on hardware,
other implementations and the open questions.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Captures from other processors (4-Series, TLS, authentication) are
especially welcome.

## License

[MIT](LICENSE)
