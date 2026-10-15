"""Test your webhook WITHOUT placing a real call (zero cost).

    uvicorn app.main:app --port 8000          # terminal 1
    python -m scripts.simulate_webhook        # terminal 2
    curl http://localhost:8000/calls          # see the resulting record

Sends: status-update (ringing, in-progress) -> transcript -> end-of-call-report.
"""
from __future__ import annotations

import argparse
import uuid

import httpx

SAMPLE_MESSAGES = [
    {"role": "bot", "message": "Hello, this is an AI assistant calling on behalf of a college fest team. Am I speaking with PrintCo Tees?", "secondsFromStart": 0.8},
    {"role": "user", "message": "Yes, Ramesh here. Tell me.", "secondsFromStart": 6.1},
    {"role": "bot", "message": "What is your best price per shirt for one hundred pieces?", "secondsFromStart": 9.0},
    {"role": "user", "message": "For hundred pieces, five hundred twenty rupees per shirt, GST extra.", "secondsFromStart": 13.5},
    {"role": "bot", "message": "Could you do better on that price?", "secondsFromStart": 18.2},
    {"role": "user", "message": "Okay, for you, four hundred ninety.", "secondsFromStart": 22.0},
]


def main(url: str, secret: str, vendor_id: str) -> None:
    call = {"id": f"sim_{uuid.uuid4().hex[:8]}",
            "metadata": {"vendor_id": vendor_id, "round": 1}}
    headers = {"X-Vapi-Secret": secret} if secret else {}
    events = [
        {"type": "status-update", "status": "ringing", "call": call},
        {"type": "status-update", "status": "in-progress", "call": call},
        {"type": "transcript", "transcriptType": "final", "role": "assistant",
         "transcript": SAMPLE_MESSAGES[0]["message"], "call": call},
        {"type": "end-of-call-report", "endedReason": "customer-ended-call", "call": call,
         "durationSeconds": 31.4, "cost": 0.21,
         "analysis": {"summary": "Vendor quoted 490 rupees per shirt after one push."},
         "artifact": {"transcript": "\n".join(f"{m['role']}: {m['message']}" for m in SAMPLE_MESSAGES),
                      "messages": SAMPLE_MESSAGES, "recordingUrl": "https://example.invalid/rec.wav"}},
    ]
    with httpx.Client(timeout=10) as http:
        for e in events:
            r = http.post(url, json={"message": e}, headers=headers)
            print(e["type"], e.get("status", ""), "->", r.status_code, r.text)
    print("\nNow run: curl http://localhost:8000/calls")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/vapi/webhook")
    ap.add_argument("--secret", default="")
    ap.add_argument("--vendor", default="printco")
    a = ap.parse_args()
    main(a.url, a.secret, a.vendor)