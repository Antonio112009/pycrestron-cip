<h1 align="center">pycrestron-cip — asyncio client for Crestron-over-IP</h1>

<p align="center">
  <b>Your code becomes a touch panel.</b> See every digital, analog and serial join a Crestron processor sends,<br>
  press any button — without touching the processor's program.
</p>

<p align="center">
  <a href="https://github.com/Antonio112009/pycrestron-cip/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Antonio112009/pycrestron-cip/ci.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI" alt="CI"></a>
  <a href="https://pypi.org/project/pycrestron-cip/"><img src="https://img.shields.io/pypi/v/pycrestron-cip?style=flat-square&logo=pypi&logoColor=white&label=PyPI&color=6366F1" alt="PyPI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/pypi/pyversions/pycrestron-cip?style=flat-square&logo=python&logoColor=white&label=Python&color=3776AB" alt="Python"></a>
  <a href="https://github.com/Antonio112009/pycrestron-cip/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/dependencies-zero-10B981?style=flat-square" alt="Zero dependencies"></a>
  <a href="https://peps.python.org/pep-0561/"><img src="https://img.shields.io/badge/typing-py.typed-0EA5E9?style=flat-square" alt="Typed"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square" alt="Ruff"></a>
  <a href="https://github.com/Antonio112009/pycrestron-cip/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Antonio112009/pycrestron-cip?style=flat-square&color=64748B&label=License" alt="License: MIT"></a>
</p>

<p align="center">
  <a href="#quick-start"><b>Quick start</b></a> ·
  <a href="#highlights"><b>Highlights</b></a> ·
  <a href="#why-pycrestron-cip"><b>Why</b></a> ·
  <a href="#api"><b>API</b></a> ·
  <a href="#testing-your-code"><b>Testing</b></a> ·
  <a href="#probing-a-processor"><b>Probe</b></a> ·
  <a href="https://github.com/Antonio112009/pycrestron-cip/blob/main/docs/protocol.md"><b>Protocol</b></a>
</p>

<br>

**CIP** (Crestron-over-IP) is the protocol that touch panels and XPanel use to talk to a Crestron control processor.
`pycrestron-cip` connects as a panel IP ID, sees what that panel would see and can press its buttons.

It needs nothing from the processor's program: no SIMPL changes, no extra modules. If a panel IP ID is defined,
you can connect to it. That makes it a good base for Home Assistant integrations and other home automation.

> [!IMPORTANT]
> **Alpha.** Tested against a Crestron 3-Series processor. The API may still change.

## Highlights

<table>
  <tr>
    <td width="33%" valign="top">
      <h3>⚡ Pure asyncio</h3>
      <p>No runtime dependencies, fully typed (<code>py.typed</code>). Drops straight into Home Assistant and other asyncio apps.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🎯 Complete state</h3>
      <p>Reads <b>every</b> join in a packed sub-packet, so the processor's initial dump arrives intact.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🔁 Self-healing</h3>
      <p>Heartbeats both ways, a liveness timeout for half-open sockets, reconnect with backoff, outputs replayed.</p>
    </td>
  </tr>
  <tr>
    <td width="33%" valign="top">
      <h3>🔘 Real buttons</h3>
      <p><code>press</code> / <code>release</code> with auto-repeat like a finger on the glass, and <code>pulse</code> for a tap.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🌍 Any text</h3>
      <p>Serial joins in UTF-8 and UTF-16, long strings in chunks, short and extended forms.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🧩 Smart objects</h3>
      <p>Incoming smart-object joins, each tagged with the object ID it came from.</p>
    </td>
  </tr>
  <tr>
    <td width="33%" valign="top">
      <h3>🧱 Correct framing</h3>
      <p>Frames split across TCP reads, several frames per read, several joins per sub-packet — all handled.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🔒 TLS &amp; auth</h3>
      <p>Optional TLS on port 41796 and username/password authentication.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🧪 Test kit</h3>
      <p><code>FakeProcessor</code> for your own tests, read-only <code>pycrestron-cip-probe</code> for real hardware.</p>
    </td>
  </tr>
</table>

## Quick start

```bash
pip install pycrestron-cip
```

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

**You need:** Python 3.13 or newer · a processor reachable on TCP 41794 (41796 for TLS) · a panel IP ID defined in
the processor's program that nothing else is using (see [below](#things-to-know-about-processors)).

<details>
<summary><b>Local development</b></summary>

```bash
git clone https://github.com/Antonio112009/pycrestron-cip.git
cd pycrestron-cip
pip install -e ".[dev]"
```

</details>

## Why pycrestron-cip

Most open-source CIP clients stop at "it connects". We read the source of every panel-side CIP client we could find,
ran their parsers against a fake processor, and built this library to close the gaps. Then we verified it on a real
processor.

<table>
  <thead>
    <tr>
      <th align="left" width="46%"></th>
      <th align="left" width="27%">pycrestron-cip</th>
      <th align="left" width="27%">Other clients¹</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td colspan="3">
        <b>🎯 Correct state from the first second</b><br>
        <sub>A processor packs several joins into one sub-packet of its initial dump. A client that reads only the first one starts with most values missing, and a paged panel may not resend them until they change. That is how a light ends up shown as off while it is on.</sub>
      </td>
    </tr>
    <tr>
      <td>Reads <b>every</b> join in a packed sub-packet</td>
      <td>✅ digital and analog,<br>verified on a 3-Series</td>
      <td>none do both; <b>6 of 7</b> keep only the first digital join</td>
    </tr>
    <tr>
      <td>Frames split across TCP reads</td>
      <td>✅</td>
      <td><b>2 of 6</b> TCP clients</td>
    </tr>
    <tr>
      <td>Smart-object joins, with the object ID</td>
      <td>✅ inbound</td>
      <td><b>0 of 7</b> complete</td>
    </tr>
    <tr>
      <td colspan="3">
        <b>🔁 Connections that heal themselves</b><br>
        <sub>A client that never notices a half-open socket can sit "connected" while nothing arrives.</sub>
      </td>
    </tr>
    <tr>
      <td>Answers the processor's heartbeat requests</td>
      <td>✅</td>
      <td><b>1 of 7</b></td>
    </tr>
    <tr>
      <td>Notices a silent, half-open connection by itself</td>
      <td>✅ liveness timeout</td>
      <td><b>0 of 7</b></td>
    </tr>
    <tr>
      <td>Reconnects with backoff</td>
      <td>✅</td>
      <td><b>2 of 7</b></td>
    </tr>
    <tr>
      <td>Replays latched outputs after a reconnect</td>
      <td>✅</td>
      <td><b>2 of 7</b></td>
    </tr>
    <tr>
      <td colspan="3">
        <b>🌍 Any text, any language</b><br>
        <sub>Room names and labels come through intact, with a safe fallback for stray bytes.</sub>
      </td>
    </tr>
    <tr>
      <td>Serial text in UTF-16 (unicode flag)</td>
      <td>✅ and UTF-8</td>
      <td><b>0 of 7</b></td>
    </tr>
    <tr>
      <td>Long strings sent in chunks</td>
      <td>✅</td>
      <td><b>0 of 7</b></td>
    </tr>
    <tr>
      <td>A non-ASCII byte cannot break the receive path</td>
      <td>✅</td>
      <td><b>3 of the 6</b> that decode serials</td>
    </tr>
    <tr>
      <td colspan="3">
        <b>🧪 Built to be depended on</b><br>
        <sub><code>FakeProcessor</code> and <code>pycrestron-cip-probe</code> let you test your integration without, and then against, real hardware.</sub>
      </td>
    </tr>
    <tr>
      <td>A fake processor you can use in your own tests</td>
      <td>✅ <code>pycrestron_cip.testing</code></td>
      <td><b>0 of 7</b> ship one</td>
    </tr>
    <tr>
      <td>Automated tests</td>
      <td>✅ 41 tests, 94 % coverage,<br>CI on Python 3.13 and 3.14</td>
      <td><b>3 of 7</b></td>
    </tr>
  </tbody>
</table>

<sub>¹ Seven XPanel-style CIP clients in Python, Swift, JavaScript and Node, each reviewed at its latest commit in
September 2026. Projects that emulate a processor, proxy TLS on the processor, or use a different protocol are not
counted. Other projects were tested against a fake processor, not real hardware.</sub>

> [!NOTE]
> **Honest limits.** Sending to smart objects is not supported yet. TLS and username/password authentication are
> implemented but not yet verified on hardware (captures welcome). The WebSocket/CH5 transport used by some 4-Series
> web panels is not covered.

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

<details>
<summary><b>Options</b></summary>
<br>

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

</details>

## Things to know about processors

- The processor sends a panel only **changes** relative to what it believes the panel already shows. After a
  (re)connect, the initial dump contains non-default values only, and a "paged" panel may not resend a value
  until it changes.
- Button-style digitals are released by the processor unless repeated within about 0.5 s. `press()` repeats
  for you until `release()`.
- Directly setting an analog that the program drives (for example a dimmer level) often does nothing: the
  program expects the panel's raise/lower buttons.

> [!WARNING]
> **Two clients on one IP ID share one panel.** On paged panels that means one shared page: one client's
> navigation changes what the other sees. Give automation its own IP ID where you can.

## Testing your code

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

## Probing a processor

`pycrestron-cip-probe` (or `python -m pycrestron_cip.probe`) connects as a panel, records everything the processor
sends and disconnects. It sends only what the protocol requires (no button presses, no join values):

```bash
pycrestron-cip-probe 192.168.1.10 0x03 --seconds 120 --log probe.log --json probe.json
```

> [!CAUTION]
> Stop anything else that uses the same IP ID first. Please do not publish probe logs from a real installation:
> they contain room names, addresses and whatever the panel shows.

## Learn more

<table>
  <tr>
    <td width="33%" valign="top">
      <h3>📘 <a href="https://github.com/Antonio112009/pycrestron-cip/blob/main/docs/protocol.md">Protocol</a></h3>
      <p>The wire format as implemented, what was verified on hardware, other implementations and open questions.</p>
    </td>
    <td width="33%" valign="top">
      <h3>🤝 <a href="https://github.com/Antonio112009/pycrestron-cip/blob/main/CONTRIBUTING.md">Contributing</a></h3>
      <p>Captures from other processors (4-Series, TLS, authentication) are especially welcome.</p>
    </td>
    <td width="33%" valign="top">
      <h3>📄 <a href="https://github.com/Antonio112009/pycrestron-cip/blob/main/LICENSE">License</a></h3>
      <p>MIT licensed.</p>
    </td>
  </tr>
</table>

<br>

<p align="center">
  <sub>Not affiliated with, endorsed by or supported by Crestron Electronics, Inc. “Crestron” is a trademark of Crestron Electronics, Inc.</sub>
</p>
