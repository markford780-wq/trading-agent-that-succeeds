"""Print an exact Kraken order ticket for a coin whose trigger has fired, plus the journal lines.

It does NOT place orders. You type the order into Kraken yourself.

  buy   limit order at the current best bid ("post-only" = maker fee 0.25% instead of 0.40%);
        size from config/rules.yaml 'allocation' x your account value; quantity rounded to Kraken's
        precision and checked against Kraken's minimum order size.
  stop  after the buy fills, place a Kraken "trailing stop" sell at the trailing % in rules.yaml.
        Kraken trails from the last trade price (intraday); the system's rule trails from the highest
        DAILY close, so Kraken's stop is slightly tighter. If you prefer the exact rule, skip Kraken's
        trailing stop and let the scheduled checks tell you when the daily-close stop is hit.

Usage: python scripts/order_ticket.py SOL [--equity 5200]
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone

import requests

from common import allocation_pct, coinmetrics, fear_greed, rules, stablecoin_supply, watchlist

KRAKEN = "https://api.kraken.com/0/public"


def kraken(path, **params):
    d = requests.get(f"{KRAKEN}/{path}", params=params, timeout=20).json()
    if d["error"]:
        raise RuntimeError(d["error"])
    return next(iter(d["result"].values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("coin")
    ap.add_argument("--equity", type=float, help="current account value in GBP (default pot_gbp)")
    args = ap.parse_args()
    cfg, wl = rules(), watchlist()
    coin = args.coin.upper()
    item = next((x for x in wl["plans"] + wl.get("universe", []) if x["coin"] == coin), None)
    if not item:
        raise SystemExit(f"{coin} is not in config/watchlist.yaml")
    pct = allocation_pct(coin, item.get("evidence"), cfg)
    if pct <= 0:
        raise SystemExit(f"{coin}: allocation is 0% (evidence: {item.get('evidence')}). The system does not buy it.")
    eq = args.equity or cfg["account"]["pot_gbp"]
    pair = item["kraken_pair"]
    info, tick = kraken("AssetPairs", pair=pair), kraken("Ticker", pair=pair)
    bid, ask = float(tick["b"][0]), float(tick["a"][0])
    quote = info["wsname"].split("/")[1]
    gbp_to_quote = 1.0 if quote == "GBP" else float(kraken("Ticker", pair="GBPUSD")["c"][0])
    size_gbp = eq * pct
    qty = math.floor(size_gbp * gbp_to_quote / bid * 10 ** info["lot_decimals"]) / 10 ** info["lot_decimals"]
    if qty < float(info["ordermin"]):
        raise SystemExit(f"Quantity {qty} is below Kraken's minimum {info['ordermin']} {coin}")
    ts = cfg["stops"]["trailing_stop_pct"]
    price_fmt = f"{{:.{info['pair_decimals']}f}}"
    now = datetime.now(timezone.utc)

    print(f"ORDER TICKET {coin} - {now:%Y-%m-%d %H:%M} UTC - review before typing into Kraken\n")
    print(f"  Pair:            {info['wsname']}")
    print(f"  Side / type:     BUY, LIMIT, post-only (maker fee {cfg['costs']['maker_fee_pct']}%)")
    print(f"  Limit price:     {price_fmt.format(bid)} {quote}  (best bid; ask {price_fmt.format(ask)}, "
          f"spread {(ask - bid) / bid * 100:.3f}%)")
    print(f"  Quantity:        {qty} {coin}")
    print(f"  Cost:            ~{qty * bid:,.2f} {quote}  (~£{qty * bid / gbp_to_quote:,.0f} = {pct:.0%} of £{eq:,.0f})")
    print(f"  If not filled within ~1 hour: cancel and re-place at the new best bid. Don't chase more than 1% above.")
    print(f"\n  After the fill:  SELL, TRAILING STOP, offset {ts}%  (initial stop ~{price_fmt.format(bid * (1 - ts / 100))} {quote})")
    print(f"  Also exit if a daily close is below the 200-day average (the scheduled checks will tell you).")

    ctx = {}
    for name, fn in (("fng", lambda: int(fear_greed().iloc[-1])),
                     ("mvrv", lambda: round(float(coinmetrics("btc", ["CapMVRVCur"])["CapMVRVCur"].iloc[-1]), 2)),
                     ("stable", lambda: round((lambda s: (s.iloc[-1] / s.iloc[-31] - 1) * 100)(stablecoin_supply()), 1))):
        try:
            ctx[name] = fn()
        except Exception:  # noqa: BLE001
            ctx[name] = ""
    print("\nAdd to journal/positions.yaml under 'positions:' AFTER the fill (use your real fill price/qty):")
    print(f"  - coin: {coin}\n    pair: {pair}\n    opened: {now:%Y-%m-%d}\n    entry_price: {price_fmt.format(bid)}"
          f"\n    quantity: {qty}\n    highest_close: {price_fmt.format(bid)}\n    targets: []\n    rule: rule_a_trend")
    print("\nAdd to journal/trades.csv (fill in the blanks; funding from the scheduled check):")
    print(f"{now:%Y-%m-%d},{coin},{pair},{price_fmt.format(bid)},{qty},{qty * bid / gbp_to_quote:.2f},,"
          f"{price_fmt.format(bid * (1 - ts / 100))},,rule_a_trend,<why>,{ctx['fng']},{ctx['mvrv']},,{ctx['stable']},,,,,,,")


if __name__ == "__main__":
    main()
