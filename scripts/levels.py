"""Position size and trade maths for each plan in config/watchlist.yaml.

Size = the smaller of (a) max_position_pct of the pot and (b) the amount whose loss at the
initial stop equals risk_per_trade_pct of the pot. Plans marked untested_half_size get half.

Usage:  python scripts/levels.py
        python scripts/levels.py --entry 72500 --stop-pct 20     # any ad-hoc trade
"""
from __future__ import annotations

import argparse
import re

from common import rules, watchlist


def position_size(pot, stop_pct, max_position_pct, risk_pct, half=False):
    by_risk = pot * risk_pct / stop_pct          # loss at stop = size * stop_pct/100 = pot * risk_pct/100
    size = min(by_risk, pot * max_position_pct / 100)
    return size / 2 if half else size


def ref_price(entry):
    if isinstance(entry, (int, float)):
        return float(entry)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(entry))]
    return sum(nums) / len(nums) if nums else None


def describe(coin, entry, stop_pct, checkpoints, cfg, half=False):
    a, c = cfg["account"], cfg["costs"]
    fee = c["taker_fee_pct"] / 100
    size = position_size(a["pot_gbp"], stop_pct, a["max_position_pct"], a["risk_per_trade_pct"], half)
    stop = entry * (1 - stop_pct / 100)
    loss = size * stop_pct / 100 + size * fee * 2
    breakeven = entry * (1 + fee) / (1 - fee)
    rr = ", ".join(f"{cp:,.6g} = {(cp - entry) / (entry - stop):.2f}R" for cp in checkpoints)
    return (f"| {coin} | {entry:,.6g} | {stop:,.6g} | £{size:,.0f}{' (half)' if half else ''} | "
            f"£{loss:,.0f} | {breakeven:,.6g} | {rr or '-'} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", type=float)
    ap.add_argument("--stop-pct", type=float)
    args = ap.parse_args()
    cfg = rules()
    ts = args.stop_pct or cfg["stops"]["trailing_stop_pct"]
    a = cfg["account"]
    print(f"Pot £{a['pot_gbp']:,}  max {a['max_position_pct']}% per trade  risk {a['risk_per_trade_pct']}% "
          f"per trade  stop {ts}%  taker fee {cfg['costs']['taker_fee_pct']}%/side\n")
    print("| Coin | Entry (USD) | Initial stop | Size | Max loss incl. fees | Break-even | Checkpoints |")
    print("|---|---:|---:|---:|---:|---:|---|")
    if args.entry:
        print(describe("ad-hoc", args.entry, ts, [], cfg))
        return
    for p in watchlist()["plans"]:
        e = ref_price(p["entry"])
        if e is None:
            continue
        print(describe(p["coin"], e, ts, p.get("checkpoints", []), cfg, p.get("evidence") == "untested_half_size"))
    print("\nR = (checkpoint - entry) / (entry - initial stop). Checkpoints are review levels; "
          "the main exit is the trailing stop or a close below the 200-day average.")


if __name__ == "__main__":
    main()
