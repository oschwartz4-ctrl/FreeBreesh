#!/usr/bin/env python3
"""
Polymarket multi-wallet tracker - one-shot version for GitHub Actions.

Runs once, compares against state.json, sends Telegram alerts, updates
state.json, and exits. The workflow commits state.json back to the repo
so the next run remembers what it already saw.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

# -------------------------------------------------------------
# CONFIG - add the last two wallets here when you have them
# -------------------------------------------------------------

WALLETS = {
    "DRose":     "0xb0c85813a7a4428f1139ff91d3118a92c391fe7f",
    "DJ":        "0x6d20c35f65d9899b6d6b74f8466e824580f9a165",
    "SwissTony": "0x204f72f35326db932158cba6adff0b9a1da95e14",
    "HomeRunHazard": "0x5268527977f700f9bf9b6d5cd843859e4e70135d",
    "RN1":           "0x2005d16a84ceefa912d4e380cd32e7ff827875ea",
}


# Fire the loud alert when this many wallets share the same position.
CONSENSUS_THRESHOLD = 3

MIN_POSITION_USD = 100        # ignore dust positions
ALERT_NEW_POSITIONS = True    # set False for overlap alerts only

# -------------------------------------------------------------

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

STATE_FILE = Path("state.json")
DATA_API = "https://data-api.polymarket.com"


def notify(text):
    print(text.replace("<b>", "").replace("</b>", ""), flush=True)
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[warn] Telegram not configured; printed only.", flush=True)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not r.ok:
            print(f"[warn] telegram {r.status_code}: {r.text[:200]}", flush=True)
    except Exception as e:
        print(f"[warn] telegram failed: {e}", flush=True)


def get_positions(address):
    """Return {conditionId|outcome: info}, or None if the fetch failed."""
    rows = None
    for attempt in range(3):
        try:
            r = requests.get(
                f"{DATA_API}/positions",
                params={"user": address, "limit": 500, "sortBy": "CURRENT"},
                timeout=25,
            )
            r.raise_for_status()
            rows = r.json()
            break
        except Exception as e:
            print(f"[warn] attempt {attempt+1} failed for {address[:10]}: {e}",
                  flush=True)
            time.sleep(3)
    if rows is None:
        return None

    out = {}
    for p in rows:
        try:
            value = float(p.get("currentValue") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value < MIN_POSITION_USD:
            continue
        cond = p.get("conditionId") or p.get("asset") or ""
        outcome = p.get("outcome") or ""
        out[f"{cond}|{outcome}"] = {
            "title": p.get("title") or "(untitled market)",
            "outcome": outcome,
            "value": value,
            "avgPrice": p.get("avgPrice"),
            "slug": p.get("slug") or "",
        }
    return out


def link(info):
    return f"https://polymarket.com/event/{info['slug']}" if info["slug"] else ""


def main():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        state = {"seen": {}, "announced": []}

    seen = state.get("seen", {})
    announced = set(state.get("announced", []))
    first_run = not seen

    threshold = min(CONSENSUS_THRESHOLD, len(WALLETS))
    current = {}
    failures = []

    for nick, addr in WALLETS.items():
        pos = get_positions(addr)
        if pos is None:
            failures.append(nick)
            current[nick] = seen.get(nick, {})
            continue
        current[nick] = pos

        if ALERT_NEW_POSITIONS and not first_run:
            for key in set(pos) - set(seen.get(nick, {})):
                i = pos[key]
                notify(
                    f"NEW - <b>{nick}</b> opened a position\n"
                    f"{i['title']}\n"
                    f"-> <b>{i['outcome']}</b> @ {i['avgPrice']} "
                    f"(${i['value']:,.0f})\n{link(i)}"
                )
        time.sleep(1)

    tally = {}
    for nick, pos in current.items():
        for key, info in pos.items():
            tally.setdefault(key, {"wallets": [], "info": info})
            tally[key]["wallets"].append(nick)

    for key, rec in tally.items():
        holders = rec["wallets"]
        if len(holders) >= threshold and key not in announced:
            i = rec["info"]
            notify(
                f"*** OVERLAP - {len(holders)}/{len(WALLETS)} wallets ***\n"
                f"{i['title']}\n"
                f"-> <b>{i['outcome']}</b>\n"
                f"Held by: {', '.join(holders)}\n{link(i)}"
            )
            announced.add(key)

    for key in list(announced):
        if key not in tally or len(tally[key]["wallets"]) < threshold:
            announced.discard(key)

    if first_run:
        overlaps = sum(1 for r in tally.values() if len(r["wallets"]) >= threshold)
        notify(
            f"Tracker initialized - {len(WALLETS)} wallets, "
            f"alerting at {threshold}+ overlap.\n"
            f"Baseline saved. Overlapping positions right now: {overlaps}"
        )

    STATE_FILE.write_text(json.dumps(
        {"seen": current, "announced": sorted(announced)}, indent=2
    ))

    if failures:
        print(f"[warn] fetch failed for: {', '.join(failures)}", flush=True)
    print("Run complete.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
