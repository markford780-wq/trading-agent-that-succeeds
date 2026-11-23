"""Live status board: trend rule status for every plan, Kraken prices/spreads, open-position stops.

Uses completed daily closes (00:00 UTC) from Binance/Kraken for the 200-day average and RSI, and
Kraken's live ticker for price and spread. Funding, news and the macro calendar are added by the
scheduled check (MCP tools), not here.

Usage:  python scripts/board.py              # print markdown
        python scripts/board.py --log        # also append to logs/checks/<UTC date>.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import requests

import patterns

from common import (ROOT, allocation_pct, candles, coinmetrics, fear_greed, load_yaml, rsi, rules, sma,
                    stablecoin_supply, watchlist)


def kraken_ticker(pair: str):
    try:
        d = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": pair}, timeout=20).json()
        if d["error"]:
            return None
        v = next(iter(d["result"].values()))
        ask, bid = float(v["a"][0]), float(v["b"][0])
        return {"last": float(v["c"][0]), "spread_pct": (ask - bid) / ((ask + bid) / 2) * 100}
    except Exception:  # noqa: BLE001
        return None


def coin_status(source, n):
    df = candles(source)
    df["sma"] = sma(df["close"], n)
    df["rsi"] = rsi(df["close"], 14)
    last = df.iloc[-1]
    chg = lambda k: (last["close"] / df["close"].iloc[-1 - k] - 1) * 100 if len(df) > k else float("nan")  # noqa: E731
    above = df["close"] > df["sma"]
    # how many consecutive completed daily closes are above the average (0 if the last one isn't)
    streak = 0
    for a in reversed(above.to_list()):
        if not a:
            break
        streak += 1
    return {"date": df.index[-1], "close": last["close"], "sma": last["sma"], "rsi": last["rsi"],
            "d7": chg(7), "d30": chg(30), "above": bool(above.iloc[-1]), "streak": streak}


def context_line():
    """Sentiment / on-chain / liquidity readings. Context only: none showed a tested edge (see README)."""
    parts = []
    try:
        parts.append(f"Fear & Greed {int(fear_greed().iloc[-1])}")
    except Exception:  # noqa: BLE001
        parts.append("Fear & Greed no data")
    try:
        parts.append(f"BTC MVRV {coinmetrics('btc', ['CapMVRVCur'])['CapMVRVCur'].iloc[-1]:.2f}")
    except Exception:  # noqa: BLE001
        parts.append("BTC MVRV no data")
    try:
        st = stablecoin_supply()
        parts.append(f"stablecoin supply ${st.iloc[-1] / 1e9:,.0f}B ({(st.iloc[-1] / st.iloc[-31] - 1) * 100:+.1f}% 30d)")
    except Exception:  # noqa: BLE001
        parts.append("stablecoin supply no data")
    return "**Context (record at entry, not a rule):** " + " · ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    args = ap.parse_args()
    cfg, wl = rules(), watchlist()
    n = cfg["regime"]["btc_trend_sma_days"]
    out = []
    now = datetime.now(timezone.utc)

    btc = coin_status("binance:BTCUSDT", n)
    regime_ok = btc["above"]
    out.append(f"### Board {now:%Y-%m-%d %H:%M} UTC (daily closes to {btc['date']})\n")
    out.append(f"**BTC regime:** close {btc['close']:,.0f} vs 200-day avg {btc['sma']:,.0f} "
               f"({(btc['close'] / btc['sma'] - 1) * 100:+.1f}%), RSI14 {btc['rsi']:.1f} -> "
               f"{'PASS: trend buys allowed' if regime_ok else 'FAIL: no new trend buys'}\n")
    out.append("| Coin | Last close | 200d avg | vs avg | RSI14 | 7d | 30d | Kraken last | Spread | Size | Status |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    max_spread = cfg["liquidity"]["max_spread_pct"]
    need = cfg.get("filters", {}).get("confirm_closes_above_sma", 1)
    for p in wl["plans"] + wl.get("universe", []):
        try:
            s = btc if p["coin"] == "BTC" else coin_status(p["history"], n)
        except Exception as e:  # noqa: BLE001
            out.append(f"| {p['coin']} | no data ({e}) |||||||||"); continue
        t = kraken_ticker(p["kraken_pair"])
        sz = allocation_pct(p["coin"], p.get("evidence"), cfg)
        vs = (s["close"] / s["sma"] - 1) * 100
        if p.get("evidence") == "not_tradable":
            status = "SKIP: no usable Kraken market"
        elif p.get("evidence") == "failed":
            status = "SKIP: trend rule failed its backtest" + (" (now above avg)" if s["above"] else "")
        elif sz <= 0:
            status = "SKIP: not in allocation (too little data)"
        elif s["sma"] != s["sma"]:  # NaN: not enough history
            status = "no 200d avg yet"
        elif not s["above"]:
            status = f"NO: {abs(vs):.1f}% below its avg"
        elif not regime_ok:
            status = "NO: BTC regime fails"
        elif s["streak"] < need:
            status = f"WAIT: {s['streak']} close(s) above avg, need {need}"
        else:
            status = "CANDIDATE: run full checklist (macro, funding, news)"
        spread = f"{t['spread_pct']:.3f}%" + (" ⚠️" if t["spread_pct"] > max_spread else "") if t else "no data"
        out.append(f"| {p['coin']} | {s['close']:,.6g} | {s['sma']:,.6g} | {vs:+.1f}% | {s['rsi']:.0f} | "
                   f"{s['d7']:+.1f}% | {s['d30']:+.1f}% | {t['last'] if t else 'no data'} ({p['kraken_pair']}) | "
                   f"{spread} | {sz:.0%} | {status} |")

    out.append("\n" + context_line())
    try:
        out.append("\n" + patterns.alert_section(patterns.history_stats()))
    except Exception as e:  # noqa: BLE001
        out.append(f"\n**Candle-pattern alerts:** no data ({e})")

    pos = load_yaml("journal/positions.yaml").get("positions") or []
    out.append("\n**Open positions:** " + ("none" if not pos else ""))
    ts = cfg["stops"]["trailing_stop_pct"] / 100
    for q in pos:
        t = kraken_ticker(q["pair"])
        hi = q["highest_close"]
        stop = hi * (1 - ts)
        if not t:
            out.append(f"- {q['coin']}: no live price"); continue
        px = t["last"]
        flag = "STOP HIT - sell and journal it" if px <= stop else (
            "near stop (<3%)" if px <= stop * 1.03 else "ok")
        hits = [tg for tg in q.get("targets", []) if px >= tg]
        out.append(f"- {q['coin']} ({q['pair']}): last {px:,.6g}, entry {q['entry_price']:,.6g} "
                   f"({(px / q['entry_price'] - 1) * 100:+.1f}%), trailing stop {stop:,.6g} -> {flag}"
                   + (f"; checkpoint(s) reached: {hits}" if hits else "")
                   + ("; raise highest_close in positions.yaml" if px > hi else ""))

    text = "\n".join(out)
    print(text)
    if args.log:
        log = ROOT / "logs" / "checks" / f"{now:%Y-%m-%d}.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            f.write(text + "\n\n")
        print(f"\n(appended to {log.relative_to(ROOT)})")


if __name__ == "__main__":
    main()
