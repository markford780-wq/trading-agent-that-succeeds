"""Test candidate entry filters on the trend rule, with a design / hold-out split.

Baseline = current rule: coin closes above its 200d SMA, BTC above its 200d SMA, 20% trailing stop
or exit on a close below the SMA (scripts/backtest.py run_trend). Each filter can only VETO an
entry (it never forces an exit). External data is lagged one day so a signal on day d's close only
uses data published by then.

Design period: up to 2022-12-31. Hold-out: 2023-01-01 onward (never used to pick thresholds).
Adoption: in the hold-out, on at least 2 of BTC/ETH/SOL, the filter improves expectancy (average
return per trade) or max drawdown, without cutting total return by more than 20% of the baseline;
and it must not cut design-period total return by more than 20% either.

Usage: python scripts/research_filters.py
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backtest import prepare, run_trend, stats
from common import coinmetrics, fear_greed, funding_history, rules, stablecoin_supply

SPLIT = date(2022, 12, 31)
COINS = {"BTC": "binance:BTCUSDT", "ETH": "binance:ETHUSDT", "SOL": "binance:SOLUSDT"}
FUNDING = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}


def lag(s: pd.Series, idx) -> pd.Series:
    """Align external daily data to idx, using only values from the previous day or earlier."""
    s = s.copy()
    s.index = pd.to_datetime(s.index)
    s = s.sort_index().shift(1, freq="D")
    return s.reindex(pd.to_datetime(list(idx)), method="ffill").set_axis(idx)


def build_filters(coin, df, btc, ext):
    c, s = df["close"], df["sma"]
    above = c > s
    f = {}
    # --- price-only filters ---
    f["confirm 2 closes"] = above & above.shift(1, fill_value=False)
    f["confirm 3 closes"] = f["confirm 2 closes"] & above.shift(2, fill_value=False)
    f["close >2% above SMA"] = c > s * 1.02
    wk = c.copy(); wk.index = pd.to_datetime(wk.index)
    weekly = wk.resample("W-SUN").last()
    for n in (20, 50):
        ok = (weekly > weekly.rolling(n).mean())
        f[f"weekly > {n}w avg"] = ok.reindex(pd.to_datetime(list(df.index)), method="ffill").fillna(False).set_axis(df.index)
    if coin != "BTC":
        ratio = c / btc["close"].reindex(df.index)
        f["rel. strength vs BTC"] = ratio > ratio.rolling(50).mean()
    else:
        f["rel. strength vs BTC"] = pd.Series(True, index=df.index)  # not applicable to BTC itself
    vol = np.log(c).diff().rolling(20).std()
    f["vol not top 10%"] = vol < vol.rolling(365, min_periods=180).quantile(0.9)
    # --- new-data filters ---
    fng = lag(ext["fng"], df.index)
    f["F&G < 75 (no extreme greed)"] = fng < 75
    f["F&G < 60"] = fng < 60
    mv = lag(ext["mvrv_eth"] if coin == "ETH" else ext["mvrv_btc"], df.index)
    f["MVRV < 3.0"] = mv < 3.0
    f["MVRV < 2.5"] = mv < 2.5
    st = lag(ext["stable"], df.index)
    f["stablecoins growing 30d"] = st > st.shift(30)
    fu = lag(ext["funding"][coin], df.index).rolling(7).mean()
    f["funding not top 10%"] = (fu < fu.rolling(365, min_periods=90).quantile(0.9)) | fu.isna()
    return {k: v.fillna(False).astype(bool) for k, v in f.items()}


def measure(df, ok, cfg):
    r = run_trend(df, ok, cfg, trailing=True)
    tot, _, dd = stats(r.equity)
    rets = [t[2] for t in r.trades]
    return {"n": len(rets), "win": 100 * np.mean([x > 0 for x in rets]) if rets else np.nan,
            "exp": 100 * np.mean(rets) if rets else np.nan, "tot": 100 * tot, "dd": 100 * dd}


def main():
    cfg = rules()
    ext = {"fng": fear_greed(), "stable": stablecoin_supply(),
           "mvrv_btc": coinmetrics("btc", ["CapMVRVCur"])["CapMVRVCur"],
           "mvrv_eth": coinmetrics("eth", ["CapMVRVCur"])["CapMVRVCur"],
           "funding": {k: funding_history(v) for k, v in FUNDING.items()}}
    btc = prepare("binance:BTCUSDT", 200)
    results = {}  # (filter, coin, period) -> metrics
    for coin, src in COINS.items():
        df = (btc if coin == "BTC" else prepare(src, 200)).dropna(subset=["sma"])
        btc_ok = (btc["close"] > btc["sma"]).reindex(df.index).fillna(False)
        filters = {"BASELINE (current rule)": pd.Series(True, index=df.index), **build_filters(coin, df, btc, ext)}
        for name, mask in filters.items():
            for period, sl in (("design", df.index <= SPLIT), ("hold-out", df.index > SPLIT)):
                d = df[sl]
                results[(name, coin, period)] = measure(d, (btc_ok & mask)[sl], cfg)

    names = list(dict.fromkeys(k[0] for k in results))
    print("Expectancy = average return per trade after fees. DD = max drawdown. Hold-out = 2023-01-01 onward.\n")
    for period in ("design", "hold-out"):
        print(f"=== {period.upper()} ===")
        print(f"{'filter':30}" + "".join(f" | {c:^34}" for c in COINS))
        print(f"{'':30}" + " | trades win%  exp%   total    DD " * len(COINS))
        for nm in names:
            row = f"{nm:30}"
            for coin in COINS:
                m = results[(nm, coin, period)]
                row += f" | {m['n']:5d} {m['win']:4.0f} {m['exp']:5.1f} {m['tot']:7.0f}% {m['dd']:4.0f}%"
            print(row)
        print()

    print("=== VERDICT (adoption rule in the docstring) ===")
    base = "BASELINE (current rule)"
    for nm in names[1:]:
        passes = 0
        notes = []
        for coin in COINS:
            b, m = results[(base, coin, "hold-out")], results[(nm, coin, "hold-out")]
            bd, md = results[(base, coin, "design")], results[(nm, coin, "design")]
            better = (m["exp"] > b["exp"]) or (m["dd"] > b["dd"])
            keeps_return = m["tot"] >= b["tot"] - 0.2 * abs(b["tot"])
            design_ok = md["tot"] >= bd["tot"] - 0.2 * abs(bd["tot"])
            ok = better and keeps_return and design_ok
            passes += ok
            notes.append(f"{coin} {'✓' if ok else '✗'}")
        print(f"{nm:30} {'ADOPT' if passes >= 2 else 'reject':6}  ({', '.join(notes)})")


if __name__ == "__main__":
    main()
