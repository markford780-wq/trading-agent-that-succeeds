"""Backtest the spot rules in config/rules.yaml on daily candles, fees included.

Rules (signals on completed daily closes, fills at the NEXT day's open, no look-ahead):
  A   trend: close > 200d SMA (+ BTC > its 200d SMA for alts). Exit: trailing stop (rules.yaml)
      (from highest close since entry, filled intraday at the stop or at the open if it gaps)
      or a close back below the 200d SMA. After a stop-out, re-entry needs a new high above
      the stopped-out peak, or a fresh cross from below the SMA.
  A0  same entry, no trailing stop: exit only on a close below the 200d SMA.
  B   staged buying: close < 200d SMA and RSI14 < 30 -> buy 1/3; next stages only after a
      further 7% drop. Exit all when close >= 200d SMA, or close 25% below average entry.

Usage:  python scripts/backtest.py            # all watchlist coins
        python scripts/backtest.py BTC --trades
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from common import candles, rsi, rules, sma, watchlist


@dataclass
class Result:
    name: str
    equity: pd.Series
    trades: list = field(default_factory=list)   # (entry_date, exit_date, return, reason)
    risk_pct: float = 10.0


def _costs(cfg):
    c = cfg["costs"]
    return c["taker_fee_pct"] / 100, c["slippage_pct"] / 100


def run_trend(df, btc_ok, cfg, trailing=True) -> Result:
    fee, slip = _costs(cfg)
    ts = cfg["stops"]["trailing_stop_pct"] / 100
    o, h, l, c, s = (df[k].to_numpy() for k in ("open", "high", "low", "close", "sma"))
    ok = btc_ok.to_numpy()
    n = len(df)
    equity = np.ones(n)
    cash, qty = 1.0, 0.0
    long = pending_buy = pending_sell = False
    peak = entry_px = 0.0
    entry_date = None
    armed, stop_peak = True, None
    trades = []

    def sell(i, px, reason):
        nonlocal cash, qty, long
        cash = qty * px * (1 - slip) * (1 - fee)
        trades.append((entry_date, df.index[i], cash / entry_cash - 1, reason))
        qty, long = 0.0, False

    entry_cash = 1.0
    for i in range(n):
        if pending_buy:
            entry_cash = cash
            entry_px = o[i] * (1 + slip)
            qty = cash * (1 - fee) / entry_px
            cash, long, peak, entry_date = 0.0, True, o[i], df.index[i]
            pending_buy = False
        elif pending_sell:
            sell(i, o[i], "close < 200d SMA")
            pending_sell = False
        if long and trailing:
            stop = peak * (1 - ts)
            if o[i] <= stop:
                sell(i, o[i], "trailing stop (gap)"); armed, stop_peak = False, peak
            elif l[i] <= stop:
                sell(i, stop, "trailing stop"); armed, stop_peak = False, peak
        if long:
            peak = max(peak, c[i])
            if not np.isnan(s[i]) and c[i] < s[i]:
                pending_sell = True
        else:
            if not np.isnan(s[i]):
                if c[i] < s[i] or (stop_peak is not None and c[i] > stop_peak):
                    armed = True
                if armed and c[i] > s[i] and ok[i] and i < n - 1:
                    pending_buy = True
        equity[i] = cash + qty * c[i]
    if long:  # mark open trade at last close
        trades.append((entry_date, df.index[-1], (qty * c[-1] * (1 - fee)) / entry_cash - 1, "open"))
    return Result(f"A trend + {ts * 100:.0f}% trail" if trailing else "A0 trend, SMA exit only",
                  pd.Series(equity, index=df.index), trades, ts * 100 if trailing else np.nan)


def run_staged(df, cfg) -> Result:
    fee, slip = _costs(cfg)
    b = cfg["signals"]["rule_b_staged"]
    stages, drop, stop_pct = b["stages"], b["next_stage_drop_pct"] / 100, b["stop_below_avg_entry_pct"] / 100
    o, c, s, r = (df[k].to_numpy() for k in ("open", "close", "sma", "rsi"))
    n = len(df)
    equity = np.ones(n)
    cash, qty, bought, last_px, cost = 1.0, 0.0, 0, None, 0.0
    start_cash = 1.0
    pending_buy = pending_sell = False
    sell_reason, entry_date = "", None
    trades = []
    for i in range(n):
        if pending_buy:
            if bought == 0:
                start_cash, entry_date = cash, df.index[i]
            spend = start_cash / stages
            px = o[i] * (1 + slip)
            qty += spend * (1 - fee) / px
            cash -= spend
            cost += spend
            bought += 1
            last_px = o[i]
            pending_buy = False
        elif pending_sell:
            cash += qty * o[i] * (1 - slip) * (1 - fee)
            trades.append((entry_date, df.index[i], cash / start_cash - 1, sell_reason))
            qty, bought, last_px, cost, pending_sell = 0.0, 0, None, 0.0, False
        if not np.isnan(s[i]) and not np.isnan(r[i]) and i < n - 1:
            if bought:
                avg_entry = cost / (qty / (1 - fee)) if qty else 0
                if c[i] >= s[i]:
                    pending_sell, sell_reason = True, "reached 200d SMA"
                elif c[i] <= avg_entry * (1 - stop_pct):
                    pending_sell, sell_reason = True, "stop 25% below avg entry"
            if not pending_sell and bought < stages and c[i] < s[i] and r[i] < b["rsi_below"]:
                if bought == 0 or c[i] <= last_px * (1 - drop):
                    pending_buy = True
        equity[i] = cash + qty * c[i]
    if bought:
        trades.append((entry_date, df.index[-1], (cash + qty * c[-1] * (1 - fee)) / start_cash - 1, "open"))
    return Result("B staged oversold buys", pd.Series(equity, index=df.index), trades, stop_pct * 100)


def stats(eq: pd.Series) -> tuple[float, float, float]:
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1
    dd = (eq / eq.cummax() - 1).min()
    return total, cagr, dd


def prepare(source, sma_days):
    df = candles(source)
    df["sma"] = sma(df["close"], sma_days)
    df["rsi"] = rsi(df["close"], 14)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("coins", nargs="*")
    ap.add_argument("--trades", action="store_true", help="list every trade")
    args = ap.parse_args()

    cfg, wl = rules(), watchlist()
    n_sma = cfg["regime"]["btc_trend_sma_days"]
    items = [(p["coin"], p["history"]) for p in wl["plans"]] + [(w["coin"], w["history"]) for w in wl.get("universe", [])]
    if args.coins:
        items = [it for it in items if it[0] in {c.upper() for c in args.coins}]

    btc = prepare("binance:BTCUSDT", n_sma)
    btc_ok = (btc["close"] > btc["sma"])

    print(f"Fees {cfg['costs']['taker_fee_pct']}% + slippage {cfg['costs']['slippage_pct']}% per side. "
          f"Signals on daily closes, fills next open.\n")
    hdr = f"{'coin':5} {'strategy':26} {'period':23} {'trades':>6} {'win%':>5} {'avgR':>5} " \
          f"{'total':>8} {'CAGR':>7} {'maxDD':>7} {'in mkt':>6} | {'B&H total':>9} {'B&H DD':>7}  verdict"
    print(hdr); print("-" * len(hdr))
    for coin, src in items:
        try:
            df = btc.copy() if src == "binance:BTCUSDT" else prepare(src, n_sma)
        except Exception as e:  # noqa: BLE001
            print(f"{coin:5} no data ({e})"); continue
        df = df.dropna(subset=["sma"])
        if len(df) < 60:
            print(f"{coin:5} not enough history after the 200-day warm-up ({len(df)} days)"); continue
        ok = btc_ok.reindex(df.index).fillna(False) if coin != "BTC" else pd.Series(True, index=df.index)
        need = cfg.get("filters", {}).get("confirm_closes_above_sma", 1)
        above = (df["close"] > df["sma"]).astype(int)
        ok = ok & (above.rolling(need).sum() == need)   # adopted confirmation filter (rules.yaml)
        bh = df["close"] / df["close"].iloc[0]
        bh_tot, _, bh_dd = stats(bh)
        for res in (run_trend(df, ok, cfg, True), run_trend(df, ok, cfg, False), run_staged(df, cfg)):
            tot, cagr, dd = stats(res.equity)
            closed = [t for t in res.trades if t[3] != "open"]
            rets = [t[2] for t in res.trades]
            win = 100 * np.mean([x > 0 for x in rets]) if rets else float("nan")
            avg_r = np.mean(rets) / (res.risk_pct / 100) if rets and not np.isnan(res.risk_pct) else float("nan")
            in_mkt = 100 * (res.equity.diff().fillna(0) != 0).mean()
            keep = tot > 0 and dd > bh_dd and (tot / abs(dd) if dd else 0) >= (bh_tot / abs(bh_dd) if bh_dd else 0)
            verdict = ("KEEP" if keep else "REJECT") + (" (inconclusive: <30 trades)" if len(rets) < 30 else "")
            period = f"{df.index[0]}..{df.index[-1]}"
            print(f"{coin:5} {res.name:26} {period:23} {len(rets):6d} {win:5.0f} {avg_r:5.2f} "
                  f"{tot:8.0%} {cagr:7.1%} {dd:7.0%} {in_mkt:5.0f}% | {bh_tot:9.0%} {bh_dd:7.0%}  {verdict}")
            if args.trades:
                for t in res.trades:
                    print(f"      {t[0]} -> {t[1]}  {t[2]:+7.1%}  {t[3]}")
        print()
    print("Verdict = KEEP when total return > 0, max drawdown smaller than buy-and-hold, and "
          "return/drawdown at least buy-and-hold's.")


if __name__ == "__main__":
    main()
