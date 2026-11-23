"""Backtest the trend rule (as configured in rules.yaml) on every universe coin and set its
'evidence' in config/watchlist.yaml.

  tested_ok           KEEP verdict with >= 3 years of data after the 200-day warm-up -> full size
  untested_half_size  KEEP with < 3 years, or < 1 year of data to test at all      -> half size
  failed              REJECT (lost money, or worse return/drawdown than buy-and-hold) -> don't trade

KEEP = total return > 0, max drawdown smaller than buy-and-hold, and return/drawdown at least
buy-and-hold's (same test as scripts/backtest.py). With 30 coins tested, a few will pass or fail
by luck: treat 'tested_ok' as "no evidence against", not proof.

Usage: python scripts/classify_universe.py            # print table only
       python scripts/classify_universe.py --write    # also update watchlist.yaml
"""
from __future__ import annotations

import argparse
import re

import pandas as pd

from backtest import prepare, run_trend, stats
from common import ROOT, rules, watchlist


def classify(coin, src, btc, btc_ok, cfg):
    need = cfg.get("filters", {}).get("confirm_closes_above_sma", 1)
    df = (btc.copy() if src == "binance:BTCUSDT" else prepare(src, 200)).dropna(subset=["sma"])
    years = len(df) / 365.25
    if len(df) < 365:
        return {"coin": coin, "years": years, "evidence": "untested_half_size", "note": "under 1 year to test"}
    ok = btc_ok.reindex(df.index).fillna(False) if coin != "BTC" else pd.Series(True, index=df.index)
    above = (df["close"] > df["sma"]).astype(int)
    ok = ok & (above.rolling(need).sum() == need)
    r = run_trend(df, ok, cfg, trailing=True)
    tot, _, dd = stats(r.equity)
    bh_tot, _, bh_dd = stats(df["close"] / df["close"].iloc[0])
    keep = tot > 0 and dd > bh_dd and (tot / abs(dd) if dd else 0) >= (bh_tot / abs(bh_dd) if bh_dd else 0)
    ev = ("tested_ok" if years >= 3 else "untested_half_size") if keep else "failed"
    return {"coin": coin, "years": years, "trades": len(r.trades), "tot": tot, "dd": dd,
            "bh_tot": bh_tot, "bh_dd": bh_dd, "evidence": ev, "note": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    cfg, wl = rules(), watchlist()
    btc = prepare("binance:BTCUSDT", 200)
    btc_ok = btc["close"] > btc["sma"]
    coins = [(p["coin"], p["history"]) for p in wl["plans"]] + [(u["coin"], u["history"]) for u in wl["universe"]]
    res = []
    for coin, src in coins:
        try:
            res.append(classify(coin, src, btc, btc_ok, cfg))
        except Exception as e:  # noqa: BLE001
            res.append({"coin": coin, "years": 0, "evidence": "untested_half_size", "note": f"no data: {e}"})
    print(f"Trend rule, {cfg['stops']['trailing_stop_pct']}% trailing stop, "
          f"{cfg.get('filters', {}).get('confirm_closes_above_sma', 1)}-close confirmation, fees included.\n")
    print("| Coin | Years tested | Trades | Rule return | Rule max DD | Buy&hold return | B&H max DD | Evidence |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")
    for r in res:
        if "tot" in r:
            print(f"| {r['coin']} | {r['years']:.1f} | {r['trades']} | {r['tot']:+.0%} | {r['dd']:.0%} | "
                  f"{r['bh_tot']:+.0%} | {r['bh_dd']:.0%} | {r['evidence']} |")
        else:
            print(f"| {r['coin']} | {r['years']:.1f} | - | - | - | - | - | {r['evidence']} ({r['note']}) |")
    if args.write:
        path = ROOT / "config" / "watchlist.yaml"
        text = path.read_text()
        for r in res:
            # never overwrite a manual 'not_tradable'
            text = re.sub(rf"(\{{coin: {re.escape(r['coin'])},[^}}]*evidence: )(?!not_tradable)\w+",
                          rf"\g<1>{r['evidence']}", text)
        path.write_text(text)
        print("\nUniverse evidence written to config/watchlist.yaml (plan coins' evidence left as set by hand).")


if __name__ == "__main__":
    main()
