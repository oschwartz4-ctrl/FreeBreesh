#!/usr/bin/env python3
"""
Polymarket multi-wallet tracker - one-shot version for GitHub Actions.

Alerts on:
  1. NEW positions at or above BIG_BET_USD.
  2. Overlap: a market+side held by 3, 4, or 5 wallets. Each tier fires
     once, and again if the overlap grows to a higher tier.

Runs once, writes state.json atomically, exits. Corrupt state is
detected and discarded rather than crashing the run.
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

ALERT_TIERS = [3, 4, 5]     # overlap counts that trigger an alert
BIG_BET_USD = 25000         # minimum size for a new-position alert
MIN_POSITION_USD = 500      # ignore anything smaller than this entirely

# -------------------------------------------------------------

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

STATE_FILE = Path("state.json")
TMP_FILE = Path("state.json.tmp")
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


def load_state():
    """Read state.json, tolerating a missing or corrupt file."""
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text())
        if not isinstance(data, dict):
            print("[warn] state.json is not an object; starting fresh", flush=True)
            return {}
        return data
    except Exception as e:
        print(f"[warn] state.json unreadable ({e}); starting fresh", flush=True)
        return {}


def save_state(seen, announced):
    """Write to a temp file first, then rename. Never leaves a partial file."""
    payload = json.dumps({"seen": seen, "announced": announced}, indent=2)
    TMP_FILE.write_text(payload)
    TMP_FILE.replace(STATE_FILE)


def get_positions(address):
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
    reached = [t for t in tiers if count >= t]
    return max(reached) if reached else 0


def main():
    state = load_state()

    seen = state.get("seen", {})
    if not isinstance(seen, dict):
        seen = {}

    announced = state.get("announced", {})
    if isinstance(announced, list):
        announced = {k: min(ALERT_TIERS) for k in announced}
    if not isinstance(announced, dict):
        announced = {}

    first_run = not seen
    tiers = sorted(t for t in ALERT_TIERS if t <= len(WALLETS)) or [len(WALLETS)]

    current = {}
    failures = []
    big_bets = 0

    for nick, addr in WALLETS.items():
        pos = get_positions(addr)
        if pos is None:
            failures.append(nick)
            current[nick] = seen.get(nick, {})
            continue
        current[nick] = pos

        if not first_run:
            for key in set(pos) - set(seen.get(nick, {})):
                i = pos[key]
                if i["value"] < BIG_BET_USD:
                    continue
                big_bets += 1
                notify(
                    f"BIG BET - <b>{nick}</b>\n"
                    f"{i['title']}\n"
                    f"-> <b>{i['outcome']}</b> @ {i['avgPrice']}\n"
                    f"Size: <b>${i['value']:,.0f}</b>\n{link(i)}"
                )
        time.sleep(1)

    # -------- overlap detection --------
    tally = {}
    for nick, pos in current.items():
        for key, info in pos.items():
            tally.setdefault(key, {"wallets": [], "info": info, "total": 0.0})
            tally[key]["wallets"].append(nick)
            tally[key]["total"] += info["value"]

    new_announced = {}
    for key, rec in tally.items():
        holders = rec["wallets"]
        count = len(holders)
        now_tier = tier_for(count, tiers)
        if now_tier == 0:
            continue

        new_announced[key] = now_tier
        prev_tier = announced.get(key, 0)
        if not isinstance(prev_tier, int):
            prev_tier = 0

        if now_tier > prev_tier:
            i = rec["info"]
            if count == len(WALLETS):
                header = f"*** ALL {count} WALLETS AGREE ***"
            else:
                header = f"*** OVERLAP - {count} of {len(WALLETS)} ***"
            escalation = f"\n(was {prev_tier}, now {count})" if prev_tier else ""
            notify(
                f"{header}\n"
                f"{i['title']}\n"
                f"-> <b>{i['outcome']}</b>\n"
                f"Held by: {', '.join(sorted(holders))}\n"
                f"Combined: ${rec['total']:,.0f}{escalation}\n{link(i)}"
            )

    if first_run:
        counts = {}
        for rec in tally.values():
            c = len(rec["wallets"])
            if c >= min(tiers):
                counts[c] = counts.get(c, 0) + 1
        summary = ", ".join(f"{v} at {k}-way" for k, v in sorted(counts.items())) or "none"
        notify(
            f"Tracker initialized - {len(WALLETS)} wallets.\n"
            f"Big-bet threshold: ${BIG_BET_USD:,}\n"
            f"Overlap tiers: {tiers}\n"
            f"Current overlaps: {summary}"
        )

    save_state(current, new_announced)

    if failures:
        print(f"[warn] fetch failed for: {', '.join(failures)}", flush=True)
    print(f"Run complete. Big bets: {big_bets}. Tracked markets: {len(tally)}.",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
