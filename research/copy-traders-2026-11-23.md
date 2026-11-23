# Can we copy the most profitable traders? (2026-11-23)

Source: Coinversa Pulse (Hyperliquid on-chain wallets), free plan.

## Method
1. Top 100 wallets by all-time realised profit (at least 500 trades) and top 100 by this month's profit.
2. Kept wallets in **both** lists, then removed:
   - **Bots and market makers:** millions of trades, or negative fees (fee rebates earned for providing liquidity).
   - **Inactive wallets.**
3. Checked each survivor's last 90 days of positions (`pulse_trader_position_summary`), asking whether its
   trades could be copied on **Kraken spot**: long only, no leverage, coins Kraken lists.

## Results (12 wallets checked)

| Wallet | All-time profit | Last 90 days | Style | Copyable on spot? |
|---|---:|---|---|---|
| 0x15a4…dfdb | +$23.5M | 2 wins, +$20.6M: **BTC long 62.4k → 78.2k, ETH long 1,762 → 2,235** (Jul 6/8 to Oct 30) | Few, multi-month long swings | **Yes**, but only 2–3 trades in 90 days, so there's almost no evidence |
| 0x7dac…f410 | +$44.9M | HYPE shorts worth $20M+, 1 liquidation | Large leveraged shorts | No (shorts) |
| 0x418a…8888 | +$38.9M | 88% win rate, 4 liquidations, BTC short, stock perps | Mixed, leveraged | No |
| 0xfb94…0ddf | +$12.7M | 84% win rate, 3 liquidations, mostly stock-perp shorts | Short stocks | No |
| 0xb0a5…9540 | +$38.2M | 4 liquidations; a −$20M ETH liquidation hidden behind a "100% win rate" | Extreme leverage | No, dangerous |
| 0x0153…8052 | +$14.9M | Shorts on ZEC, UNI, PONS | Shorts | No |
| 0x7a6d…304f | +$10.5M | 452 positions, 16 liquidations, short-biased scalping | High-frequency | No |
| 0xb695…2d8b | +$21.7M | Shorts on stocks (SNDK, META, NBIS) | Shorts | No |
| 0x7be1…80d8 | +$13.5M | 1 position in 90 days | Inactive | No |
| 0x5d2f…9bb7 | +$60.1M | **−$0.2M realised in 90 days** | Fading | No |
| 0x45d2…fed4 | +$40.0M | **−$0.9M realised**, 46 open positions | Fading | No |

## Conclusions
- **Only 1 of 12 top persistent wallets trades in a way you can copy on spot.** The rest make their money from
  shorts, leverage (several liquidations) or bot-speed trading.
- **Leaderboards mislead:**
  - "Win rate" is counted per fill, not per trade, and can hide huge liquidations (wallet 0xb0a5).
  - 2 of 12 all-time winners are already losing in the last 90 days, which is survivorship bias.
- **Copying always lags:** you see the trade after it's made, at a worse price.
- **A list of 100 wallets to copy trade by trade would be mostly uncopyable or unproven.** I didn't build one.

## What would let us test "follow the smart money" properly
- **Starter plan ($29/month):** `live_cohort_bias` shows whether the most profitable tier (Apex/Sharps) is net long or short each coin right now.
- **Pro plan ($199/month):** `live_cohort_bias_history` (90 days) and `pulse_persistent_winners`. Only with history can we
  test whether following the Apex tier's long bias would have beaten our trend rule.

Until then, watch wallet 0x15a4f009bb324a3fb9e36137136b201e3fe0dfdb as context only. It has 1 open position;
check it with `pulse_trader_position_summary`.
