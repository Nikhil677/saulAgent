"""Step 1: create/update the buyer + simulated vendor assistants and phone numbers.

    python -m scripts.bootstrap               # assistants + free Vapi numbers
    python -m scripts.bootstrap --no-numbers  # assistants only (web-call testing)

Safe to re-run: ids are stored in state/state.json and updated in place.
"""
from __future__ import annotations

import argparse
import asyncio

from app.assistants import bootstrap
from app.call_manager import load_json
from app.config import Settings
from app.store import JsonStore
from app.vapi_client import VapiClient


async def main(create_numbers: bool) -> None:
    s = Settings.from_env()
    if not s.webhook_url:
        print("WARNING: PUBLIC_BASE_URL is empty. Assistants are created WITHOUT a webhook, so you "
              "will need `scripts.launch_round --poll` to read results.\n")
    vendors = load_json(s.config_dir / "vendors.json")
    async with VapiClient(s.vapi_api_key, s.vapi_base_url) as client:
        state = await bootstrap(client, s, vendors, JsonStore(s.state_dir / "state.json"), create_numbers)
    print("Buyer assistant :", state["buyer_assistant_id"])
    print("Buyer number    :", state.get("buyer_number", "(not created)"))
    for vid, v in state.get("vendors", {}).items():
        print(f"Vendor {vid:<11}: assistant={v['assistant_id']} number={v.get('number', '-')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-numbers", action="store_true")
    asyncio.run(main(create_numbers=not ap.parse_args().no_numbers))