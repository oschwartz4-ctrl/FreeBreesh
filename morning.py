#!/usr/bin/env python3
"""Sends a short morning greeting via Telegram."""

import os
import random
from datetime import datetime, timedelta, timezone

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

LINES = [
    "Go get 'em, tiger.",
    "Today's the kind of day that rewards showing up.",
    "Big things start with small mornings.",
    "You've done harder than whatever today's got.",
    "Patience is a position too.",
    "Slow is smooth, smooth is fast.",
    "Don't chase it. Let it come to you.",
    "The edge is in the waiting.",
    "Discipline beats conviction. Every time.",
    "Play your game, not theirs.",
    "Good decisions, then good outcomes. In that order.",
    "Make it a day worth writing down.",
    "Head up. Eyes open. Let's go.",
    "The best move is often no move.",
    "You don't have to catch every wave.",
    "Steady hands today.",
    "Trust the process you built.",
    "Nothing needs to happen before coffee.",
    "Bet on yourself first.",
    "Some mornings you just have to start. Start.",
]


def main():
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[warn] Telegram not configured.")
        return

    now_utc = datetime.now(timezone.utc)
    offset = -4 if 3 <= now_utc.month <= 10 else -5
    local = now_utc + timedelta(hours=offset)
    day = local.strftime("%A, %B %d")

    msg = f"Good morning, The Big O. {day}.\n\n{random.choice(LINES)}"

    print(msg)
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT_ID, "text": msg},
        timeout=20,
    )
    if not r.ok:
        print(f"[warn] telegram {r.status_code}: {r.text[:200]}")


if __name__ == "__main__":
    main()

