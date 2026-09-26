# CIP protocol notes

Crestron does not publish CIP. Everything here comes from captures against a 3-Series
processor and from reading other open-source implementations (see the end of this page).
Where sources disagree, both views are noted. **Verified** marks behaviour we observed ourselves.

## Framing

- Every frame: `[type:1][length:2 BE][payload]`.
- TCP 41794 (plain); **41796 = TLS** (self-signed certificates); CH5/WebXPanel uses a WebSocket on 49200 (not implemented).
- Most payloads start with a 2-byte handle, always `00 00` on TCP so far.
- The processor may **coalesce** several frames into one TCP read and **split** a frame across reads (verified). A client needs an incremental framer.

## Frame types

| Type | Direction | Meaning |
|---|---|---|
| 0x01 | panel → proc | Connect: `00 00 00 00 00 <ipid> 40 ff ff f1 01` (modern form: extended serial, unicode, heartbeat). The legacy 7-byte form makes the processor answer with legacy encodings |
| 0x02 | proc → panel | Connect result. `ff ff 02` = IP ID not defined (also seen while the program boots). Success: `00 00 00 1f` (verified), `00 00 20 0f` and `00 00 00 03` have also been reported. Treat anything other than `ff ff 02` as success |
| 0x03 | both | Disconnect (the processor sends it on program stop/reload) |
| 0x05 | both | Data: `[handle:2]` then one or more Cresnet sub-packets `[len:1][type:1][data]` |
| 0x0A | panel → proc | Alternate connect used by Crestron apps (not used here) |
| 0x0B / 0x0C | | Auth `00 00 "user:pass" 00 00 00` / result `[handle:2][access level]` (0 = rejected). Order on TCP is unverified |
| 0x0D / 0x0E | both | Heartbeat / heartbeat response `00 00`. **The processor also sends 0x0D, and it must be answered** |
| 0x0F | proc → panel | Program status right after TCP accept: 0 = loading, 1 = not running, 2 = ready. Register only on 2 |
| 0x12 | both | Extended data: `[handle:2]` then `[len:2][type:1][data]` (extended serial 0x34, smart-object serial 0x39) |

## Cresnet sub-packets

| Type | Meaning | Data |
|---|---|---|
| 0x00 | Digital | `<lo> <hi>`: join-1 as 15-bit little endian; bit 0x80 of `hi` set = **low** |
| 0x27 | Digital, button style | Same as 0x00. The processor auto-releases unless it is repeated within ~0.5 s |
| 0x14 | Analog | `<join-1:2 BE> <value:2 BE>` |
| 0x01 | Analog, legacy | `<join-1:1> <value:2>` or `<join-1:2> <value:2>` |
| 0x15 / 0x34 | Serial short / extended | `<join-1:2 BE> <flags> <bytes>`. Flags: 0x01 start, 0x02 end, 0x04 UTF-16LE |
| 0x03 | Command | `00` update request, `16` penultimate, `1c` end-of-query, `1d` end-of-query ack |
| 0x08 | Date/time | BCD time and date |
| 0x38 / 0x39 | Smart object | `00 00 00 <object id>` then nested sub-packets |

## Session

1. TCP connect, then the processor sends `0f 00 01 02` (program ready).
2. The panel sends connect (0x01). The processor answers 0x02.
3. (If authentication is enabled) 0x0B → 0x0C.
4. The panel sends an update request (`05 00 05 00 00 02 03 00`).
5. The processor echoes it, sends date/time and **only non-default joins**, then `03 16` and `03 1c`.
6. The panel answers `03 1d` and a heartbeat. It is now synced.
7. Heartbeats every ~15 s. No traffic for ~35 s means the link is dead.

**Verified** on a 3-Series: after the initial dump the processor sends a panel only values that
**differ from what it believes the panel shows**. Values on "paged" panels, where the same joins
show different rooms, therefore arrive only when they change relative to the previous page.

**Verified** on a 3-Series (2026-09-26, read-only probe, `pycrestron-cip-probe`):
- The initial dump **packs several joins into one sub-packet**: `0f 00` + 7 digital pairs, `09 14` + 2 analog
  pairs, smart-object blocks of 32 digitals. `python-cipclient` (and this library before the fix) read only the
  first pair, losing most of the dump. Live changes afterwards arrived one per packet.
- The processor never sent a heartbeat request (0x0D) in 2 minutes; it only answered ours (0x0E).
- Connect result was `0000001f`; program status on connect `0f 02` (ready). Registration to synced: 0.7 s.
- Command `03 19` right after the dump carries 2-byte big-endian numbers: `434c 434d` (17228, 17229) and
  `6a42 6a57 6a59 6a65` (27202, 27223, 27225, 27237). Then the processor sends digitals 27213, 27217, 27218,
  27221 = off (twice). Both look like the panel's reserved/system join ranges (17xxx, 27xxx), not program
  joins. Meaning unconfirmed: the library keeps the bytes in `CommandMessage.data` and logs them at DEBUG.

## Prior art

| Project | Language | License | Notes |
|---|---|---|---|
| [klenae/python-cipclient](https://github.com/klenae/python-cipclient) | Python, threads | MIT | The reference most others copy. PyPI 0.0.2 (2020): no carry-over buffer, only `0000001f` accepted, a non-ASCII serial kills the receive thread, no answer to 0x0D |
| [rdriscoll/Crestron-CIP](https://github.com/rdriscoll/Crestron-CIP) | C# | MIT | The only processor-side implementation; smart objects, short/long/unicode serial |
| [cbw/SwiftCrestronCIP](https://github.com/cbw/SwiftCrestronCIP) | Swift | MIT | cipclient port with encoder tests |
| [rellis-erigon/Crestron-CIP-HA](https://github.com/rellis-erigon/Crestron-CIP-HA) | Python asyncio | MIT | Field notes on registration timeouts and unclean disconnects |
| [siegeld/pycrestron](https://github.com/siegeld/pycrestron) | Python asyncio | unclear | WebSocket/CH5 transport (port 49200, JWT), not TCP CIP |
| [akellai/scip2cip](https://github.com/akellai/scip2cip) | C# | none | Only source for TLS 41796 + 0x0B auth |
| [CommandFusion/CIP](https://github.com/CommandFusion/CIP) | JS | none | Oldest client; legacy connect; 0xff 0xff 0x02 during boot |
| [npope/home-assistant-crestron-component](https://github.com/npope/home-assistant-crestron-component) | Python | Apache-2.0 | Different approach: a SIMPL XSIG module connects to Home Assistant |

This library is an independent implementation. No code was copied; byte vectors in the tests
come from our own captures and from the behaviour documented above.

## Open questions (to verify on hardware)

- The TCP order of the 0x0B/0x0C auth exchange, and whether 4-Series processors require 41796.
- Unicode serials from the processor (flag 0x04 / UTF-16LE) and long serials split into chunks.
- The meaning of the connect-result variants (`1f`, `200f`, `03`).
- Two sessions on one IP ID: shared, or does the second one kick the first?
