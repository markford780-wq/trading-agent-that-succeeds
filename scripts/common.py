"""Shared helpers: config loading, daily candle download/cache, indicators."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
DAY_MS = 86_400_000


def load_yaml(rel: str) -> dict:
    with open(ROOT / rel) as f:
        return yaml.safe_load(f)


def rules() -> dict:
    return load_yaml("config/rules.yaml")


def watchlist() -> dict:
    return load_yaml("config/watchlist.yaml")


# ---------- candles ----------

def _binance_daily(symbol: str, start: str = "2017-01-01") -> pd.DataFrame:
    url = "https://data-api.binance.vision/api/v3/klines"
    start_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    rows = []
    while True:
        r = requests.get(url, params={"symbol": symbol, "interval": "1d",
                                      "startTime": start_ms, "limit": 1000}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows += [[b[0], *map(float, b[1:6])] for b in batch]
        start_ms = batch[-1][0] + DAY_MS
        if len(batch) < 1000:
            break
        time.sleep(0.2)
    return _frame(rows)


def _kraken_daily(pair: str) -> pd.DataFrame:
    """Kraken returns at most the last 720 daily bars."""
    r = requests.get("https://api.kraken.com/0/public/OHLC",
                     params={"pair": pair, "interval": 1440}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data["error"]:
        raise RuntimeError(f"Kraken {pair}: {data['error']}")
    key = next(k for k in data["result"] if k != "last")
    rows = [[int(b[0]) * 1000, *map(float, b[1:5]), float(b[6])] for b in data["result"][key]]
    return _frame(rows)


def _frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.date
    return df.drop(columns="t").set_index("date")


def candles(source: str, refresh: bool = True, drop_open_bar: bool = True) -> pd.DataFrame:
    """Daily candles for 'binance:BTCUSDT' or 'kraken:XMRUSD'.

    Cached under data/cache. drop_open_bar removes today's unfinished candle so every
    signal uses completed daily closes only (00:00 UTC close).
    """
    venue, symbol = source.split(":")
    path = CACHE / f"{venue}_{symbol}.csv"
    if refresh or not path.exists():
        df = _binance_daily(symbol) if venue == "binance" else _kraken_daily(symbol)
        CACHE.mkdir(parents=True, exist_ok=True)
        df.to_csv(path)
    else:
        df = pd.read_csv(path, index_col="date", parse_dates=False)
        df.index = pd.to_datetime(df.index).date
    if drop_open_bar and len(df) and df.index[-1] >= datetime.now(timezone.utc).date():
        df = df.iloc[:-1]
    return df


# ---------- indicators ----------

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + gain / loss)
