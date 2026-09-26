# pycrestron-cip

asyncio client for **CIP** (Crestron-over-IP), the protocol touch panels and XPanel use to
talk to a Crestron control processor. Your code connects as a panel IP ID, sees what the
panel would see (digital, analog and serial joins) and can press buttons, as a panel can.

> Status: **alpha**, tested against a Crestron 3-Series processor. The API may still change.
>
> Not affiliated with, endorsed by or supported by Crestron Electronics, Inc.
> "Crestron" is a trademark of Crestron Electronics, Inc.

## Features

- Pure asyncio, no runtime dependencies, typed (`py.typed`), Python 3.13+.
- Correct TCP framing: frames split across reads or several frames in one read.
- Registration, update request / end-of-query sync, and heartbeats both ways.
- Liveness timeout: a silent (half-open) connection is noticed and reconnected.
- Automatic reconnect with backoff. A long retry interval for an undefined IP ID.
- Join caches for inputs. Latched outputs are replayed after every reconnect.
- Buttons: `press` / `release` with auto-repeat, and `pulse`.
- Serial joins in UTF-8 and UTF-16 (unicode flag), chunked long strings, short and extended forms, smart objects.
- Optional TLS (port 41796) and username/password authentication.
- `pycrestron_cip.testing.FakeProcessor`: a CIP server for your own tests.

## Install

Not on PyPI yet. From a checkout:

```bash
pip install -e .
```

## Usage

```python
import asyncio
from pycrestron_cip import CipClient, JoinType

async def main():
    async with CipClient("192.168.1.10", 0x03) as panel:
        panel.subscribe(lambda u: print(u.type, u.join, u.value))
        await panel.pulse(101)                # tap button d101
        print(panel.get_analog(361))          # current value of a361
        panel.set_serial(10, "hello")         # latched serial output
        await asyncio.sleep(10)

asyncio.run(main())
```

| Call | What it does |
|---|---|
| `await connect()` / `async with` | Connect, register, wait for the initial sync. Raises `IpidNotDefinedError`, `AuthError`, `CipTimeoutError` or `CipConnectionError` |
| `get_digital/analog/serial(join)` | Last value the processor sent (default when never sent) |
| `subscribe(cb, join_type=None, join=None)` | Callback for incoming joins; returns an unsubscribe function |
| `subscribe_state(cb)` | Connection state changes (`ConnectionState`) |
| `set_digital/analog/serial(join, value)` | Latched outputs, replayed after reconnect |
| `press(join)` / `release(join)` / `await pulse(join)` | Buttons |
| `request_update()` | Ask the processor to send its values again |
| `available`, `state`, `wait_ready()` | Connection status |
| `snapshot()` | Diagnostics (no credentials) |

Options: `port`, `ssl=True` (TLS on 41796 without certificate checks, or pass an
`SSLContext`), `username`/`password`, `connect_timeout`, `heartbeat_interval` (15 s),
`liveness_timeout` (35 s), `reconnect`, `backoff_min`/`backoff_max`, `button_repeat` (0.5 s).

### Things to know about processors

- The processor sends a panel only **changes** relative to what it thinks the panel already shows. After a
  (re)connect the initial dump holds non-default values only, and a panel page may not resend
  values until they change.
- Button-style digitals (0x27) are auto-released by the processor unless repeated within ~0.5 s.
- Two clients on the same IP ID share one panel "page". Use a dedicated IP ID for automation.

## Protocol

See [docs/protocol.md](docs/protocol.md) for the wire format as implemented and for
other implementations and what we learned from them.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[test]' ruff
.venv/bin/pytest
.venv/bin/ruff check .
```

## License

MIT
