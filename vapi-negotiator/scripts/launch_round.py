"""Step 2: place a round of calls from the command line.

    python -m scripts.launch_round --round 1 --poll
    python -m scripts.launch_round --round 2 --vendors printco \
        --leverage printco="another vendor offered 430 rupees per shirt with 7 day delivery" --poll

--poll   wait for the calls to finish by polling GET /call/{id} (no webhook/tunnel needed).
NOTE: this script writes state/calls.json. Stop the API server first (or run with --poll only for
      quick tests) so the two processes do not overwrite each other's records.
"""
from __future__ import annotations

import argparse
import asyncio

from app.call_manager import CallManager, load_json
from app.config import Settings
from app.main import build_manager
from app.vapi_client import VapiClient


def parse_leverage(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items or []:
        vendor, _, quote = item.partition("=")
        if not quote:
            raise SystemExit(f"--leverage must look like vendor_id=quote text, got: {item}")
        out[vendor.strip()] = quote.strip().strip('"')
    return out


async def main(a: argparse.Namespace) -> None:
    s = Settings.from_env()
    async with VapiClient(s.vapi_api_key, s.vapi_base_url) as client:
        m: CallManager = build_manager(s, client)
        vendor_ids = a.vendors.split(",") if a.vendors else None
        recs = await m.launch_round(a.round, vendor_ids, parse_leverage(a.leverage))
        for r in recs:
            print(f"[{r.vendor_id}] record={r.record_id} call={r.call_id} status={r.status.value} {r.error or ''}")
        if a.poll:
            print("\nWaiting for calls to finish...")
            await m.wait_until_done(recs, timeout=a.timeout)
            for r in recs:
                print(f"\n=== {r.vendor_name}: {r.status.value} ({r.ended_reason}) "
                      f"{r.duration_sec}s cost={r.cost} ===")
                for t in r.turns:
                    ts = f"{t.start_sec:6.1f}s" if t.start_sec is not None else "   -   "
                    print(f"{ts}  {t.role:<6} {t.text}")
                if r.summary:
                    print("summary:", r.summary)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--vendors", help="comma-separated vendor ids (default: all)")
    ap.add_argument("--leverage", nargs="*", help='vendor_id="competing quote sentence"')
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--timeout", type=float, default=600)
    asyncio.run(main(ap.parse_args()))