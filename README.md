# trading-agent-that-succeeds

A rules-first **spot** crypto trading system run with Claude. It finds, checks and tracks trades.
**It never places orders.** Every buy and sell is your decision, and nothing here is financial advice.

## How it works

1. **Rules** live in [`config/rules.yaml`](config/rules.yaml): pot size, position size, risk per trade,
   trailing stop, the BTC trend rule, the macro wait window, fees and the maximum spread.
2. **Plans** live in [`config/watchlist.yaml`](config/watchlist.yaml): what has to happen before buying
   each coin, plus entry, stop, checkpoints and the cancel condition.
3. **Scheduled checks** run 3 times a day, attached to the Claude session that set them up:

   | UTC   | Purpose |
   |-------|---------|
   | 00:20 | Just after the 00:00 UTC daily close. The only run that can **confirm** a "close above" trigger |
   | 13:50 | 20 minutes after US data (13:30 UTC). Catches the market's reaction |
   | 21:10 | After the US stock close, about 3 hours before the daily close. Warns if a trigger is close |

   Each run executes `scripts/board.py --log`, then adds futures funding (Hyperliquid and CoinMarketCap),
   the US macro calendar, news, and the full pre-trade checklist for any CANDIDATE. Results are written to
   `logs/checks/<date>.md`.
4. **Journal**: record a trade in [`journal/positions.yaml`](journal/positions.yaml) when you buy, and in
   [`journal/trades.csv`](journal/trades.csv) when you sell. The checks watch open positions' trailing stops.

## The rules, and the evidence behind them

Run `python scripts/backtest.py`. It uses daily candles, includes Kraken taker fees (0.40%) and 0.05%
slippage per side, acts on signals from daily closes, and fills at the next day's open.

| Rule | Result (2018 to Nov 2026) | Decision |
|---|---|---|
| **A. Trend:** buy when the coin closes above its 200-day average and BTC is above its own; exit on the trailing stop or a close below the average | BTC, ETH, SOL: much smaller drawdowns than buying and holding with a 20% stop (BTC -55% vs -77%, ETH -44% vs -90%, SOL -48% vs -96%). Fails on LINK, TRX, TAO | **On** for BTC, ETH, SOL. Half size for HYPE and XMR (under a year of data). Off for LINK, TRX, TAO |
| **Trailing stop width** | A 10% stop is too tight for BTC (+83% vs +430% with no stop). Results jump around between 10% and 30%, so there is no single "optimal" value | **20%** (middle choice, not tuned to the best result) |
| **B. Staged oversold buying** (RSI below 30, below the 200-day average) | BTC -20% vs +432% buy-and-hold. SOL -57% | **Off** |
| **Wait for a 2nd close above the average** (`research_filters.py`) | 2023–26 hold-out: average profit per trade BTC 6.6% to 8.0%, SOL 7.1% to 7.7%, ETH 14.2% to 12.3%. 2018–22: better on all 3 | **On**. A small edge that cuts one-day false breakouts |
| **New data used as filters:** Fear & Greed, MVRV (Coin Metrics), stablecoin supply (DefiLlama), funding history (Binance), weekly trend, relative strength, volatility | None improved the 2023–26 hold-out on top of the trend rule. Weekly > 20-week average looked best in 2018–22 (BTC +374% vs +137%) and then failed 2023–26 | **Rejected as rules.** Shown on the board as context and logged at entry, to be reviewed after 20 or more real trades |

### Top-30 coverage (`scripts/classify_universe.py`)
All 30 tradable top-30 coins are listed on Kraken. The trend rule (20% trailing stop, 2-close confirmation)
was backtested on each one:
- **Full size:** BTC, ETH, SOL, AVAX, HBAR, SHIB, PEPE. The rule beat buying and holding on risk-adjusted return with at least 3 years of data.
- **Half size:** HYPE, CC, CRO, ASTER, ONDO. Too little history to judge.
- **Don't trade with this rule:** BNB, XRP, TRX, ZEC, XMR, DOGE, LINK, ADA, XLM, BCH, NEAR, LTC, UNI, GRAM, SUI, TAO, AAVE.
  It lost money or lagged buy-and-hold badly on these.
- **Not tradable:** M (Kraken's `MUSD` market isn't MemeCore).

Testing 30 coins means a few will pass or fail by luck, so read "full size" as "no evidence against".

### Candle patterns (`scripts/patterns.py`)
The script finds 11 classic daily patterns on every coin and compares the following 10 days with that
coin's normal 10-day return. Results are pooled across coins: 2018–22 design, 2023–26 hold-out. Only
**three white soldiers** held up: +4.6% above normal in the hold-out, t = 2.3, 247 cases. Bullish engulfing
(−1.1%) and hammer (−0.8%) were followed by *worse* than normal returns. The board alerts on validated
patterns that have **formed** (last daily close) or are **forming** (today's unfinished candle at the
current price), and only counts the rest.

### Twitter / X (XPOZ)
This is a live check only, for coins that become candidates: compare the last 24 hours of `$TICKER` tweets
with the daily average of the previous 7 days. A spike of more than 3× with no news behind it is flagged as
hype. It can't be backtested cheaply, because that would take thousands of daily counts, and the Fear & Greed
index, which already includes social data, showed no edge.

### Filter research method
`python scripts/research_filters.py` picks rules on 2018–2022 and tests them only on 2023 onward, which
the rules were never fitted to. A filter is adopted only if it improves profit per trade or drawdown on
at least 2 of BTC, ETH and SOL in the hold-out, without cutting total return by more than 20%. Paid
on-chain data (Glassnode, CryptoQuant) is **not** worth buying yet: the free MVRV filter never even
triggered in 2023–26, so there's no evidence that more on-chain data would add an edge to this rule.

Limits: every test covers one 2018–2026 history, and the 10–30% stop sweep shows results are sensitive
to luck and path. Most results have fewer than 30 trades, which is too few for confidence. Trend rules
win less than half their trades and make their money on a few long trends, so expect several small
losses in a row.

## Position sizing

`python scripts/levels.py`: size = the smaller of 20% of the pot and the amount that loses **2% of the
pot** at the initial stop. With a 20% stop that's 10% of the pot. Untested coins get half that.

## Commands

```bash
pip install -r requirements.txt
```

```bash
python scripts/board.py
```

```bash
python scripts/levels.py
```

```bash
python scripts/backtest.py BTC --trades
```

## Data sources

Binance public data (`data-api.binance.vision`, `data.binance.vision` for funding), Kraken (live prices),
alternative.me (Fear & Greed), Coin Metrics community API (MVRV) and DefiLlama (stablecoin supply).
All are free and allowed in this repo's cloud environment. During checks Claude also uses
CoinMarketCap, CoinGecko, Coinversa (Hyperliquid), FXMacroData and TextToQuant.

## Adding a plan

Add an entry under `plans:` in `config/watchlist.yaml` with `history` (for example `binance:ETHUSDT`)
and `kraken_pair`. Run `python scripts/backtest.py <COIN>`, and only trade it at full size if the trend
rule passes the verdict.
