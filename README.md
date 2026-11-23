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

Binance public data (`data-api.binance.vision`) supplies history and Kraken's public API supplies live
prices and spreads; both are allowed in this repo's cloud environment. During checks Claude also uses
CoinMarketCap, CoinGecko, Coinversa (Hyperliquid), FXMacroData and TextToQuant.

## Adding a plan

Add an entry under `plans:` in `config/watchlist.yaml` with `history` (for example `binance:ETHUSDT`)
and `kraken_pair`. Run `python scripts/backtest.py <COIN>`, and only trade it at full size if the trend
rule passes the verdict.
