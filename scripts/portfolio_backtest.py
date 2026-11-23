"""Whole-system backtest: every coin together, with the real account limits.

Rules (from config/rules.yaml): BTC above its 200-day average; coin has N consecutive closes above its
own 200-day average; enter at next open; exit on trailing stop (intraday) or a close below the average;
re-entry after a stop needs a new high or a fresh cross. Position size = % of current equity (full or
half), at most max_open_positions at once; when more coins signal than there are free slots, the
strongest 90-day performers are taken first. Fees + slippage on every fill.

Coin selection, two ways:
  honest   coins chosen using ONLY data up to 2022-12-31 (trend rule passed there = full size, too
           little history then = half size, failed = excluded), then traded from 2023-01-01. This is
           what you could have done in real time.
  current  coins chosen with today's evidence (watchlist.yaml) over all history. In-sample: flatters
           results, shown only for comparison.

Also: a drawdown brake (halve new position sizes while equity is more than X% below its peak) and a
block-bootstrap stress test of the daily returns.

Usage: python scripts/portfolio_backtest.py
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backtest import prepare, run_trend, stats
from common import rules, watchlist

SPLIT = date(2022, 12, 31)


def load_universe(n_sma):
    wl = watchlist()
    coins = {}
    for x in wl["plans"] + wl.get("universe", []):
        if x.get("evidence") == "not_tradable":
            continue
        try:
            df = prepare(x["history"], n_sma)
        except Exception:  # noqa: BLE001
            continue
        df.index = pd.to_datetime(df.index)
        coins[x["coin"]] = (df, x.get("evidence"))
    return coins


def design_selection(coins, btc_ok, cfg, need):
    """Evidence using only data up to SPLIT."""
    sel = {}
    for c, (df, _) in coins.items():
        d = df[df.index <= pd.Timestamp(SPLIT)].dropna(subset=["sma"])
        if len(d) < 365:
            sel[c] = "half"          # too little history at the time -> half size
            continue
        ok = btc_ok.reindex(d.index).fillna(False) if c != "BTC" else pd.Series(True, index=d.index)
        above = (d["close"] > d["sma"]).astype(int)
        ok = ok & (above.rolling(need).sum() == need)
        r = run_trend(d, ok, cfg, True)
        tot, _, dd = stats(r.equity)
        bh, _, bhdd = stats(d["close"] / d["close"].iloc[0])
        keep = tot > 0 and dd > bhdd and (tot / abs(dd)) >= (bh / abs(bhdd) if bhdd else 0)
        sel[c] = "full" if keep else "excluded"
    return sel


def simulate(coins, sizes, btc_ok, cfg, start, end, full_pct, half_pct, max_pos, brake_dd=None):
    fee = (cfg["costs"]["taker_fee_pct"] + cfg["costs"]["slippage_pct"]) / 100
    ts = cfg["stops"]["trailing_stop_pct"] / 100
    need = cfg.get("filters", {}).get("confirm_closes_above_sma", 1)
    names = [c for c in coins if sizes.get(c) in ("full", "half") or isinstance(sizes.get(c), float)]
    idx = pd.date_range(start, end, freq="D")
    O, H, L, C, S, CONF, MOM = {}, {}, {}, {}, {}, {}, {}
    for c in names:
        df = coins[c][0].reindex(idx)
        O[c], H[c], L[c], C[c], S[c] = (df[k].to_numpy() for k in ("open", "high", "low", "close", "sma"))
        above = (df["close"] > df["sma"]).astype(float)
        CONF[c] = (above.rolling(need).sum() == need).to_numpy()
        MOM[c] = (df["close"] / df["close"].shift(90) - 1).to_numpy()
    bok = btc_ok.reindex(idx).fillna(False).to_numpy()

    cash, peak_eq = 1.0, 1.0
    pos = {}                     # coin -> dict(qty, peak, entry_val, entry_date)
    armed = {c: True for c in names}
    stop_peak = {c: None for c in names}
    pend_buy, pend_sell = [], set()
    equity, trades = np.ones(len(idx)), []

    def close_pos(c, px, i, why):
        nonlocal cash
        p = pos.pop(c)
        val = p["qty"] * px * (1 - fee)
        cash += val
        trades.append((c, p["date"], idx[i].date(), val / p["cost"] - 1, why))

    for i in range(len(idx)):
        # 1. queued exits at the open
        for c in list(pend_sell):
            if c in pos and not np.isnan(O[c][i]):
                close_pos(c, O[c][i], i, "close < 200d avg")
            pend_sell.discard(c)
        # 2. intraday trailing stops
        for c in list(pos):
            if np.isnan(L[c][i]):
                continue
            stop = pos[c]["peak"] * (1 - ts)
            if O[c][i] <= stop or L[c][i] <= stop:
                stop_peak[c], armed[c] = pos[c]["peak"], False
                close_pos(c, min(O[c][i], stop) if O[c][i] <= stop else stop, i, "trailing stop")
        # 3. queued entries at the open
        eq_open = cash + sum(p["qty"] * (O[c][i] if not np.isnan(O[c][i]) else p["last"]) for c, p in pos.items())
        scale = 0.5 if (brake_dd and eq_open < peak_eq * (1 - brake_dd)) else 1.0
        for c in pend_buy:
            if len(pos) >= max_pos or c in pos or np.isnan(O[c][i]):
                continue
            v = sizes[c]
            pct = (v if isinstance(v, float) else full_pct if v == "full" else half_pct) * scale
            spend = min(cash, eq_open * pct)
            if spend <= 0:
                continue
            cash -= spend
            pos[c] = {"qty": spend * (1 - fee) / O[c][i], "peak": O[c][i], "cost": spend,
                      "date": idx[i].date(), "last": O[c][i]}
        pend_buy = []
        # 4. close: update, then signals for tomorrow
        for c, p in pos.items():
            if not np.isnan(C[c][i]):
                p["last"] = C[c][i]
                p["peak"] = max(p["peak"], C[c][i])
                if not np.isnan(S[c][i]) and C[c][i] < S[c][i]:
                    pend_sell.add(c)
        cands = []
        for c in names:
            if c in pos or np.isnan(C[c][i]) or np.isnan(S[c][i]):
                continue
            if C[c][i] < S[c][i] or (stop_peak[c] is not None and C[c][i] > stop_peak[c]):
                armed[c] = True
            if armed[c] and CONF[c][i] and bok[i]:
                cands.append((np.nan_to_num(MOM[c][i], nan=-9), c))
        free = max_pos - len(pos) + len(pend_sell)
        pend_buy = [c for _, c in sorted(cands, reverse=True)[:max(free, 0)]]
        equity[i] = cash + sum(p["qty"] * p["last"] for p in pos.values())
        peak_eq = max(peak_eq, equity[i])
    for c in list(pos):
        trades.append((c, pos[c]["date"], idx[-1].date(), pos[c]["qty"] * pos[c]["last"] * (1 - fee) / pos[c]["cost"] - 1, "open"))
    return pd.Series(equity, index=idx), trades


def report(name, eq, trades, bench):
    tot, cagr, dd = stats(eq)
    btot, bcagr, bdd = stats(bench)
    rets = [t[3] for t in trades]
    yearly = eq.resample("YE").last().pct_change()
    yearly.iloc[0] = eq.resample("YE").last().iloc[0] / eq.iloc[0] - 1
    yr = " ".join(f"{d.year}:{v:+.0%}" for d, v in yearly.items())
    print(f"{name:42} {tot:+8.0%} {cagr:7.1%} {dd:6.0%} | trades {len(rets):3d} win {np.mean([r > 0 for r in rets]) * 100 if rets else 0:3.0f}% "
          f"avg {np.mean(rets) * 100 if rets else 0:+5.1f}% | BTC hold {btot:+.0%} (DD {bdd:.0%})")
    print(f"{'':42} by year: {yr}")


def stress(eq, runs=2000, block=20, seed=7):
    r = eq.pct_change().dropna().to_numpy()
    rng = np.random.default_rng(seed)
    n = len(r)
    dds, cagrs = [], []
    for _ in range(runs):
        starts = rng.integers(0, n - block, n // block + 1)
        path = np.concatenate([r[s:s + block] for s in starts])[:n]
        curve = np.cumprod(1 + path)
        dds.append((curve / np.maximum.accumulate(curve) - 1).min())
        cagrs.append(curve[-1] ** (365.25 / n) - 1)
    q = lambda a, p: np.percentile(a, p)  # noqa: E731
    return {"dd50": q(dds, 50), "dd5": q(dds, 5), "dd1": q(dds, 1),
            "cagr5": q(cagrs, 5), "cagr50": q(cagrs, 50), "loss_prob": np.mean(np.array(cagrs) < 0)}


def main():
    cfg = rules()
    n = cfg["regime"]["btc_trend_sma_days"]
    need = cfg.get("filters", {}).get("confirm_closes_above_sma", 1)
    al = cfg["allocation"]
    max_pos = cfg["account"]["max_open_positions"]
    coins = load_universe(n)
    btc = coins["BTC"][0]
    btc_ok = btc["close"] > btc["sma"]
    end = max(df.index[-1] for df, _ in coins.values())

    def to_pcts(sel):
        out = {}
        for c, v in sel.items():
            if c in al["core"]:
                out[c] = al["core"][c] / 100
            elif v == "full" and al["satellite_pct"]:
                out[c] = al["satellite_pct"] / 100
            elif v == "half" and al["half_size_coins"]:
                out[c] = al["half_size_coins"] / 100
        return out

    honest = design_selection(coins, btc_ok, cfg, need)
    current = {c: {"tested_ok": "full", "untested_half_size": "half"}.get(ev, "excluded") for c, (_, ev) in coins.items()}
    hp, cp = to_pcts(honest), to_pcts(current)
    print("Honest selection (data to 2022 only):", ", ".join(f"{c} {p:.0%}" for c, p in hp.items()))
    print("Today's selection:                   ", ", ".join(f"{c} {p:.0%}" for c, p in cp.items()))
    print(f"Max {max_pos} open. Fees+slippage {cfg['costs']['taker_fee_pct'] + cfg['costs']['slippage_pct']:.2f}%/side.\n")
    print(f"{'system':42} {'total':>8} {'CAGR':>7} {'maxDD':>6}")
    hs = pd.Timestamp("2023-01-01")
    bench_h = btc["close"].reindex(pd.date_range(hs, end)).ffill()
    eq, tr = simulate(coins, hp, btc_ok, cfg, hs, end, 0, 0, max_pos)
    report("HONEST hold-out 2023+ (real-time choice)", eq, tr, bench_h)
    s = pd.Timestamp("2018-03-04")
    bench_a = btc["close"].reindex(pd.date_range(s, end)).ffill()
    eqc, trc = simulate(coins, cp, btc_ok, cfg, s, end, 0, 0, max_pos)
    report("2018+ with today's picks (in-sample)", eqc, trc, bench_a)
    for label, e in (("honest 2023+", eq), ("2018+", eqc)):
        st = stress(e)
        print(f"\nStress test, {label} (2,000 reshuffled histories, 20-day blocks):")
        print(f"  max drawdown: typical {st['dd50']:.0%}, 1 in 20 {st['dd5']:.0%}, 1 in 100 {st['dd1']:.0%}")
        print(f"  yearly return: typical {st['cagr50']:.1%}, 1 in 20 {st['cagr5']:.1%}; chance of a loss: {st['loss_prob']:.0%}")


if __name__ == "__main__":
    main()
