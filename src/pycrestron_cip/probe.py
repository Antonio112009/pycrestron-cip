"""Read-only probe: connect as a panel, record everything the processor sends, disconnect.

Sends only what the protocol requires (connect, update request, end-of-query ack, heartbeats,
disconnect). No button presses, no join values.

    python -m pycrestron_cip.probe HOST IPID [--seconds 120] [--log probe.log] [--json probe.json]
    pycrestron-cip-probe HOST IPID ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Sequence
from typing import Any

from .client import CipClient, ConnectionState
from .exceptions import CipError
from .protocol import JoinUpdate


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="pycrestron-cip-probe",
        description="Connect as a panel, record everything the processor sends (read-only), disconnect.",
    )
    ap.add_argument("host", help="processor address")
    ap.add_argument("ipid", type=lambda s: int(s, 0), help="panel IP ID, e.g. 0x03")
    ap.add_argument("--port", type=int, help="TCP port (default 41794)")
    ap.add_argument("--seconds", type=float, default=120, help="how long to listen after the sync (default 120)")
    ap.add_argument("--log", default="probe.log", help="raw frame log (default probe.log)")
    ap.add_argument("--json", default="probe.json", help="events, stats and snapshot (default probe.json)")
    return ap.parse_args(argv)


async def probe(args: argparse.Namespace) -> dict[str, Any]:
    """Run one probe session; writes ``args.json`` and returns the same data."""
    t0 = time.monotonic()
    events: list[dict[str, Any]] = []

    def on_join(u: JoinUpdate) -> None:
        events.append(
            {"t": round(time.monotonic() - t0, 3), "type": str(u.type), "join": u.join, "value": u.value,
             "so": u.smart_object}
        )  # fmt: skip

    def on_state(s: ConnectionState) -> None:
        events.append({"t": round(time.monotonic() - t0, 3), "state": str(s)})
        print(f"{time.monotonic() - t0:7.2f}s state {s}", flush=True)

    client = CipClient(args.host, args.ipid, port=args.port, reconnect=False)
    client.subscribe(on_join)
    client.subscribe_state(on_state)
    try:
        await client.connect()
        dump = sum(1 for e in events if "join" in e)
        print(
            f"synced in {time.monotonic() - t0:.2f}s, initial dump {dump} joins; listening {args.seconds:.0f}s",
            flush=True,
        )
        await asyncio.sleep(args.seconds)
    finally:
        await client.close()
        result = {"events": events, "stats": client.stats, "last_error": client.last_error,
                  "snapshot": client.snapshot()}  # fmt: skip
        with open(args.json, "w", encoding="utf-8") as f:  # noqa: ASYNC230 - one write at exit
            json.dump(result, f, default=str, ensure_ascii=False, indent=1)
        print("stats", client.stats, "last_error", client.last_error, flush=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        filename=args.log,
        level=logging.DEBUG,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        asyncio.run(probe(args))
    except CipError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
