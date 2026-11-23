"""Live results vs what the backtest says to expect, and the drawdown review line.

Reads journal/trades.csv (closed trades), journal/positions.yaml (open, valued at Kraken prices)
and journal/equity.csv (daily account values). Flags:
  - REVIEW: account more than review_drawdown_pct below its peak -> stop new buys, review the system
  - losing streak longer than the backtest's worst
  - live average trade far below the backtest's after 20+ trades

Usage: python scripts/performance.py            # report
       python scripts/performance.py --record   # also append today's account value to journal/equity.csv
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd
import requests

from common import ROOT, load_yaml, rules

# From scripts/portfolio_backtest.py (honest 2023+ hold-out, core+satellite design)
# Longest losing streak in the backtest was 8 (both 2018+ and 2023+); flag only beyond 10 to allow for chance.
EXPECTED = {"win_rate": 0.34, "avg_trade": 0.118, "worst_losing_streak": 8, "streak_flag": 10}


def kraken_last(pair):
    try:
        d = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": pair}, timeout=20).json()
        return float(next(iter(d["result"].values()))["c"][0])
    except Exception:  # noqa: BLE001
        return None


def gbp_per(pair):
    if pair.endswith("GBP"):
        return 1.0
    r = kraken_last("GBPUSD")
    return 1 / r if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    cfg = rules()
    pot = cfg["account"]["pot_gbp"]
    trades = pd.read_csv(ROOT / "journal" / "trades.csv")
    closed = trades[trades["date_closed"].notna() & (trades["date_closed"].astype(str) != "")]
    realized = pd.to_numeric(closed["pnl_gbp"], errors="coerce").fillna(0).sum()

    pos = load_yaml("journal/positions.yaml").get("positions") or []
    invested, open_val = 0.0, 0.0
    for p in pos:
        px, fx = kraken_last(p["pair"]), gbp_per(p["pair"])
        cost = p["entry_price"] * p["quantity"] * (fx or 0)
        invested += cost
        open_val += (px or p["entry_price"]) * p["quantity"] * (fx or 0)
    equity = pot + realized + (open_val - invested)

    eq_path = ROOT / "journal" / "equity.csv"
    hist = pd.read_csv(eq_path) if eq_path.exists() else pd.DataFrame(columns=["date", "equity_gbp"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.record:
        hist = pd.concat([hist[hist["date"] != today], pd.DataFrame([{"date": today, "equity_gbp": round(equity, 2)}])])
        hist.to_csv(eq_path, index=False)
    peak = max([pot, equity] + pd.to_numeric(hist["equity_gbp"], errors="coerce").dropna().tolist())
    dd = equity / peak - 1
    review = cfg["allocation"]["review_drawdown_pct"] / 100

    print(f"**Account:** £{equity:,.0f} (start £{pot:,.0f}, realised £{realized:+,.0f}, open positions "
          f"£{open_val - invested:+,.0f}) · peak £{peak:,.0f} · drawdown {dd:.1%}")
    if dd <= -review:
        print(f"**REVIEW:** account is {abs(dd):.0%} below its peak (line: {review:.0%}). Stop new buys and review the system.")

    n = len(closed)
    if n == 0:
        print(f"**Live trades:** none closed yet. Backtest expects about {EXPECTED['win_rate']:.0%} winners "
              f"and +{EXPECTED['avg_trade']:.0%} average per trade; most trades lose small, a few win big.")
        return
    r = pd.to_numeric(closed["pnl_pct"], errors="coerce").dropna() / 100
    streak = best = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    print(f"**Live trades:** {n} closed · win rate {(r > 0).mean():.0%} (expected ~{EXPECTED['win_rate']:.0%}) · "
          f"average {r.mean():+.1%} (expected ~{EXPECTED['avg_trade']:+.0%}) · longest losing streak {best} "
          f"(backtest worst ~{EXPECTED['worst_losing_streak']})")
    if best > EXPECTED["streak_flag"]:
        print("**REVIEW:** losing streak is well beyond the backtest's worst (8).")
    if n >= 20 and r.mean() < 0:
        print("**REVIEW:** 20+ trades and the average trade is negative; the live edge may be gone.")


if __name__ == "__main__":
    main()
