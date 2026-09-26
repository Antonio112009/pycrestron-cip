"""Read-only probe: connect as a panel, record everything the processor sends, disconnect.

Sends only what the protocol requires (connect, update request, end-of-query ack, heartbeats,
disconnect). No button presses, no join values.

    python examples/probe.py HOST IPID [--seconds 120] [--log probe.log] [--json probe.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from pycrestron_cip import CipClient, ConnectionState, JoinUpdate


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("ipid", type=lambda s: int(s, 0))
    ap.add_argument("--port", type=int)
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--log", default="probe.log")
    ap.add_argument("--json", default="probe.json")
    args = ap.parse_args()

    logging.basicConfig(filename=args.log, level=logging.DEBUG,
                        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s %(message)s", datefmt="%H:%M:%S")
    t0 = time.monotonic()
    events: list[dict] = []

    def on_join(u: JoinUpdate) -> None:
        events.append({"t": round(time.monotonic() - t0, 3), "type": str(u.type), "join": u.join,
                       "value": u.value, "so": u.smart_object})

    def on_state(s: ConnectionState) -> None:
        events.append({"t": round(time.monotonic() - t0, 3), "state": str(s)})
        print(f"{time.monotonic() - t0:7.2f}s state {s}", flush=True)

    client = CipClient(args.host, args.ipid, port=args.port, reconnect=False)
    client.subscribe(on_join)
    client.subscribe_state(on_state)
    try:
        await client.connect()
        synced = time.monotonic() - t0
        dump = sum(1 for e in events if "join" in e)
        print(f"synced in {synced:.2f}s, initial dump {dump} joins; listening {args.seconds:.0f}s", flush=True)
        await asyncio.sleep(args.seconds)
    finally:
        await client.close()
        snap = client.snapshot()
        with open(args.json, "w") as f:  # noqa: ASYNC230 - one write at exit
            json.dump({"events": events, "stats": client.stats, "last_error": client.last_error,
                       "snapshot": snap}, f, default=str, ensure_ascii=False, indent=1)
        print("stats", client.stats, "last_error", client.last_error, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
