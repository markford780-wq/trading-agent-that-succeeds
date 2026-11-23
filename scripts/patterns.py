"""Candlestick patterns: how they played out in the past, and alerts when one forms again.

1. History: finds 11 classic daily patterns on every coin in the watchlist and measures what
   happened next (10-day forward return vs that coin's normal 10-day return), pooled across coins.
   Design period up to 2022-12-31; hold-out from 2023-01-01.
   A pattern is VALIDATED only if, in BOTH periods, it moved price in its expected direction by
   more than the round-trip trading cost, with at least 30 hold-out cases and |t| >= 2 in the
   hold-out. Forward windows overlap, so t-stats are optimistic: treat VALIDATED as "worth
   watching", not a signal on its own. Spot only: bearish patterns can only mean "don't buy / consider
   selling", never "short".
2. Alerts:
   - FORMED: pattern completed on the last daily close (00:00 UTC).
   - FORMING: today's unfinished candle would complete a pattern if it closed at the current price.

Usage: python scripts/patterns.py              # history table + current alerts
       python scripts/patterns.py --alerts     # alerts only (uses cached history stats)
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import pandas as pd

from common import CACHE, candles, rules, watchlist

SPLIT = date(2022, 12, 31)
HORIZON = 10
STATS_FILE = CACHE / "pattern_stats.json"

# direction: +1 bullish (expect up), -1 bearish (expect down), 0 neutral (indecision)
DIRECTION = {
    "bullish engulfing": 1, "hammer": 1, "morning star": 1, "three white soldiers": 1,
    "bearish engulfing": -1, "shooting star": -1, "evening star": -1, "three black crows": -1,
    "doji": 0, "inside bar": 0, "outside bar": 0,
}


def detect(df: pd.DataFrame) -> pd.DataFrame:
    """Boolean column per pattern, True on the candle that completes it."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body, rng = c - o, (h - l).replace(0, np.nan)
    ab = body.abs()
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    bull, bear = body > 0, body < 0
    down5, up5 = c < c.shift(5), c > c.shift(5)
    p = pd.DataFrame(index=df.index)
    p["bullish engulfing"] = bear.shift(1) & bull & (o <= c.shift(1)) & (c >= o.shift(1)) & down5
    p["bearish engulfing"] = bull.shift(1) & bear & (o >= c.shift(1)) & (c <= o.shift(1)) & up5
    p["hammer"] = (lower >= 0.6 * rng) & (upper <= 0.15 * rng) & down5
    p["shooting star"] = (upper >= 0.6 * rng) & (lower <= 0.15 * rng) & up5
    p["doji"] = ab <= 0.1 * rng
    big1, small1 = ab.shift(2) > 0.5 * rng.shift(2), ab.shift(1) < 0.3 * rng.shift(1)
    mid2 = (o.shift(2) + c.shift(2)) / 2
    p["morning star"] = bear.shift(2) & big1 & small1 & bull & (c > mid2)
    p["evening star"] = bull.shift(2) & big1 & small1 & bear & (c < mid2)
    strong = ab > 0.5 * rng
    p["three white soldiers"] = (bull & strong & (c > c.shift(1))) & (bull & strong & (c > c.shift(1))).shift(1) \
        & (bull & strong & (c > c.shift(1))).shift(2)
    p["three black crows"] = (bear & strong & (c < c.shift(1))) & (bear & strong & (c < c.shift(1))).shift(1) \
        & (bear & strong & (c < c.shift(1))).shift(2)
    p["inside bar"] = (h < h.shift(1)) & (l > l.shift(1))
    p["outside bar"] = (h > h.shift(1)) & (l < l.shift(1))
    return p.fillna(False).astype(bool)


def universe():
    wl = watchlist()
    return [(x["coin"], x["history"], x.get("evidence")) for x in wl["plans"] + wl.get("universe", [])]


def history_stats() -> dict:
    cost = 2 * (rules()["costs"]["taker_fee_pct"] + rules()["costs"]["slippage_pct"]) / 100
    rows = []  # (pattern, coin, period, excess, hit)
    per_coin = {}
    for coin, src, ev in universe():
        if ev == "not_tradable":
            continue
        try:
            df = candles(src, refresh=False)
        except Exception:  # noqa: BLE001
            continue
        fwd = df["close"].shift(-HORIZON) / df["close"] - 1
        pats = detect(df)
        for period, sl in (("design", df.index <= SPLIT), ("hold-out", df.index > SPLIT)):
            base = fwd[sl].mean()
            for name in DIRECTION:
                hits = pats[name][sl] & fwd[sl].notna()
                ex = (fwd[sl][hits] - base).to_list()
                rows += [(name, coin, period, x) for x in ex]
                per_coin.setdefault(coin, {}).setdefault(name, {})[period] = (len(ex), float(np.mean(ex)) if ex else None)
    r = pd.DataFrame(rows, columns=["pattern", "coin", "period", "excess"])
    out = {"cost": cost, "horizon": HORIZON, "patterns": {}, "per_coin": per_coin}
    for name, d in DIRECTION.items():
        res = {}
        for period in ("design", "hold-out"):
            x = r[(r.pattern == name) & (r.period == period)]["excess"]
            n = len(x)
            m = float(x.mean()) if n else float("nan")
            t = float(m / (x.std() / np.sqrt(n))) if n > 2 and x.std() > 0 else float("nan")
            res[period] = {"n": n, "mean_excess": m, "t": t}
        if d == 0:
            ok = False
        else:
            ok = all(res[p]["n"] > 0 and d * res[p]["mean_excess"] > cost for p in res) \
                and res["hold-out"]["n"] >= 30 and d * res["hold-out"]["t"] >= 2
        out["patterns"][name] = {"direction": d, **res, "validated": bool(ok)}
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(out, indent=1, default=float))
    return out


def load_stats() -> dict:
    return json.loads(STATS_FILE.read_text()) if STATS_FILE.exists() else history_stats()


def alerts(stats: dict) -> list[tuple[bool, str]]:
    """(validated, text) for patterns completed on the last close (FORMED) or forming in today's
    unfinished candle (FORMING)."""
    lines = []
    for coin, src, ev in universe():
        if ev == "not_tradable":
            continue
        try:
            full = candles(src, refresh=False, drop_open_bar=False)
        except Exception:  # noqa: BLE001
            continue
        today = pd.Timestamp.now(tz="UTC").date()
        has_open = len(full) and full.index[-1] >= today
        closed = full.iloc[:-1] if has_open else full
        for label, frame in (("FORMED", closed), ("FORMING", full if has_open else None)):
            if frame is None or len(frame) < 10:
                continue
            last = detect(frame).iloc[-1]
            for name in [k for k, v in last.items() if v and DIRECTION[k] != 0]:
                s = stats["patterns"][name]
                pc = stats["per_coin"].get(coin, {}).get(name, {}).get("hold-out")
                coin_txt = f"{coin} hold-out n={pc[0]}, avg {pc[1] * 100:+.1f}%" if pc and pc[0] else f"{coin}: no hold-out cases"
                tag = "VALIDATED" if s["validated"] else "not validated (no proven edge)"
                lines.append((s["validated"], f"- **{label}** {coin} {name} ({'bullish' if s['direction'] > 0 else 'bearish'}) "
                             f"[{frame.index[-1]}] - {tag}. Pooled hold-out: {s['hold-out']['mean_excess'] * 100:+.2f}% vs normal "
                             f"over {stats['horizon']}d (n={s['hold-out']['n']}, t={s['hold-out']['t']:.1f}); {coin_txt}"))
    return lines


def report(stats: dict) -> str:
    out = [f"**Candle-pattern history** ({stats['horizon']}-day return vs normal, all coins pooled; "
           f"round-trip cost {stats['cost'] * 100:.1f}%)\n",
           "| Pattern | Dir | Design n | Design avg | Hold-out n | Hold-out avg | Hold-out t | Verdict |",
           "|---|---|---:|---:|---:|---:|---:|---|"]
    for name, s in stats["patterns"].items():
        d, ho = s["design"], s["hold-out"]
        out.append(f"| {name} | {'+' if s['direction'] > 0 else '-' if s['direction'] < 0 else '0'} | {d['n']} | "
                   f"{d['mean_excess'] * 100:+.2f}% | {ho['n']} | {ho['mean_excess'] * 100:+.2f}% | {ho['t']:.1f} | "
                   f"{'VALIDATED' if s['validated'] else 'no edge'} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerts", action="store_true", help="alerts only, from cached stats")
    args = ap.parse_args()
    stats = load_stats() if args.alerts else history_stats()
    if not args.alerts:
        print(report(stats) + "\n")
    print(alert_section(stats, show_all=True))


def alert_section(stats: dict, show_all: bool = False) -> str:
    al = alerts(stats)
    good = [t for v, t in al if v]
    other = [t for v, t in al if not v]
    out = ["**Candle-pattern alerts** (validated patterns only: " +
           ", ".join(k for k, v in stats["patterns"].items() if v["validated"]) + ")"]
    out += good or ["- none formed or forming"]
    if other:
        out.append(f"- {len(other)} other pattern(s) formed/forming with NO proven edge"
                   + (":" if show_all else " (python scripts/patterns.py lists them)"))
        if show_all:
            out += ["  " + t for t in other]
    return "\n".join(out)


if __name__ == "__main__":
    main()
