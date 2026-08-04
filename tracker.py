#!/usr/bin/env python3
"""
Polymarket multi-wallet tracker - one-shot version for GitHub Actions.

Runs once, compares against state.json, sends Telegram alerts, updates
state.json, and exits. The workflow commits state.json back to the repo
so the next run remembers what it already saw.

Consensus alerts fire at each new tier: 3, 4, and 5 wallets. If an
overlap grows from 3 to 4 to 5, you get a separate alert each time.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

# -------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------

WALLETS = {
    "#1 Ferrari":       "0xfe787d2da716d60e8acff57fb87eb13cd4d10319",
    "#2 RN1":           "0x2005d16a84ceefa912d4e380cd32e7ff827875ea",
    "#3 2C":            "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563",
    "#4 SwissTony":     "0x204f72f35326db932158cba6adff0b9a1da95e14",
    "#5 HomeRunHazard": "0x5268527977f700f9bf9b6d5cd843859e4e70135d",
}

# Alert at each of these overlap counts.
ALERT_TIERS = [3, 4, 5]

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


def tier_for(count, tiers):
    """Highest tier this count has reached, or 0."""
    reached = [t for t in tiers if count >= t]
    return max(reached) if reached else 0


def main():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        state = {}

    seen = state.get("seen", {})
    # announced maps position key -> highest tier already alerted
    announced = state.get("announced", {})
    if isinstance(announced, list):        # migrate from old format
        announced = {k: 3 for k in announced}

    first_run = not seen

    tiers = sorted(t for t in ALERT_TIERS if t <= len(WALLETS))
    if not tiers:
        tiers = [len(WALLETS)]

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

    # -------- overlap detection, tiered --------
    tally = {}
    for nick, pos in current.items():
        for key, info in pos.items():
            tally.setdefault(key, {"wallets": [], "info": info})
            tally[key]["wallets"].append(nick)

    new_announced = {}
    for key, rec in tally.items():
        holders = rec["wallets"]
        count = len(holders)
        now_tier = tier_for(count, tiers)
        if now_tier == 0:
            continue

        new_announced[key] = now_tier
        prev_tier = announced.get(key, 0)

        if now_tier > prev_tier:
            i = rec["info"]
            if count == len(WALLETS):
                header = f"*** ALL {count} WALLETS AGREE ***"
            else:
                header = f"*** OVERLAP - {count} of {len(WALLETS)} wallets ***"
            escalation = ""
            if prev_tier > 0:
                escalation = f"\n(was {prev_tier}, now {count})"
            notify(
                f"{header}\n"
                f"{i['title']}\n"
                f"-> <b>{i['outcome']}</b>\n"
                f"Held by: {', '.join(sorted(holders))}{escalation}\n"
                f"{link(i)}"
            )

    announced = new_announced

    if first_run:
        counts = {}
        for rec in tally.values():
            c = len(rec["wallets"])
            if c >= min(tiers):
                counts[c] = counts.get(c, 0) + 1
        summary = ", ".join(f"{v} at {k}-way" for k, v in sorted(counts.items())) or "none"
        notify(
            f"Tracker initialized - {len(WALLETS)} wallets.\n"
            f"Alert tiers: {tiers}\n"
            f"Current overlaps: {summary}"
        )

    STATE_FILE.write_text(json.dumps(
        {"seen": current, "announced": announced}, indent=2
    ))

    if failures:
        print(f"[warn] fetch failed for: {', '.join(failures)}", flush=True)
    print("Run complete.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
