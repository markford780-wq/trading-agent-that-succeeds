"""Position sizes and trade maths for every tradable coin (config/rules.yaml 'allocation').

Size = allocation % x current account value (core coins BTC/ETH, satellites for other 'tested_ok'
coins). Shows the initial trailing stop, the loss if it is hit, and the fee-adjusted break-even.

Usage:  python scripts/levels.py                 # uses pot_gbp from rules.yaml
        python scripts/levels.py --equity 5400   # current account value, if different
"""
from __future__ import annotations

import argparse

from common import allocation_pct, rules, watchlist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, help="current account value in GBP (default: pot_gbp)")
    args = ap.parse_args()
    cfg = rules()
    eq = args.equity or cfg["account"]["pot_gbp"]
    ts = cfg["stops"]["trailing_stop_pct"]
    fee = cfg["costs"]["taker_fee_pct"] / 100
    wl = watchlist()
    print(f"Account £{eq:,.0f} · trailing stop {ts}% · taker fee {fee * 100:.2f}%/side "
          f"(limit orders: {cfg['costs']['maker_fee_pct']}%)\n")
    print("| Coin | Role | Size % | Size £ | Loss if initial stop hit (incl. fees) | % of account |")
    print("|---|---|---:|---:|---:|---:|")
    total = 0
    for x in wl["plans"] + wl.get("universe", []):
        pct = allocation_pct(x["coin"], x.get("evidence"), cfg)
        if pct <= 0:
            continue
        size = eq * pct
        loss = size * ts / 100 + size * fee * 2
        role = "core" if x["coin"] in cfg["allocation"]["core"] else "satellite"
        total += pct
        print(f"| {x['coin']} | {role} | {pct:.0%} | £{size:,.0f} | £{loss:,.0f} | {loss / eq:.1%} |")
    print(f"\nIf every signal were on at once: {total:.0%} of the account invested "
          f"(max {cfg['account']['max_open_positions']} positions; strongest 90-day performers first).")
    print("Break-even after fees: entry x {:.4f}.".format((1 + fee) / (1 - fee)))


if __name__ == "__main__":
    main()
