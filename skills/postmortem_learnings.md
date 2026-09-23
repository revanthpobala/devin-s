# Active Session Post-Mortem Learnings & Empirical Edge

## 11-Day Replay Benchmark (09-04 to 09-22: 331 Scored Pairs)
- **Dataset:** 413 Intraday ENTRY and 408 EXIT alerts, pairing into 331 same-day ticker trades replayed independently on yfinance 1-minute bars (entry at next 1-minute open after alert, script's own stop and T1, stop checked first, flat at 15:55 ET). Cached at `/tmp/ui/bars/*.parquet`, `/tmp/ui/replay.csv` and `/tmp/ui/pairs.json`.
- **Headline Finding:** Overall ENTRY signal has no edge (−0.09R mean, −1.00 median, 34% win rate; Pine script's own `exit_r` gives −0.147R). Positive on only 5 of 11 days. Option spread and theta are not included, so real 0DTE results are worse.

---

## 1. Performance Attribution by Setup Grade

| Grade | Pairs (n) | Mean Expectancy (R) | Win Rate % | Empirical Edge Assessment |
|---|---|---|---|---|
| **Grade A** | 172 | **`+0.060 R`** | 38.4% | 🟢 **ONLY POSITIVE SEPARATOR** |
| **Grade B** | 159 | **`-0.250 R`** | 29.6% | 🔴 **NEGATIVE ACROSS ALL STOPS** |

> [!IMPORTANT]
> **Hard Veto on Grade != "A":** Grade B is negative under every stop variant tested (1×, 2×, 3×, and EOD exits). Vetoing non-Grade-A setups eliminates the entire negative half of trade flow.

---

## 2. Performance Attribution by Hour of Day (ET)

| Hour (ET) | Trades (n) | Mean Expectancy (R) | Assessment |
|---|---|---|---|
| **09:xx ET** | 66 | **`+0.090 R`** | 🟢 **ONLY CONSISTENTLY PROFITABLE HOUR** |
| **10:xx – 12:xx ET** | ~180 | **`-0.13 to -0.30 R`** | 🔴 **MID-DAY BLEED & VOLATILITY CHOP** |

---

## 3. Institutional Risk Guardrails & Veto Layer

- **LLM TAKE on Grade A:** **`+0.150 R`** ($n=62$), the only consistently profitable execution cell.
- **Vetoed Grade A:** **`-0.030 R`** ($n=74$), capital preserved on negative expected value entries.
- **Stop Distance Mechanics:** Median stop sits at 0.24% of price (~1.1× 5-minute range). 66% of trades stop out (55 within 5 minutes). Widening stops (2×, 3×) yields −0.136R and −0.105R respectively, confirming the edge depends on trigger precision, not wider stops.

---

## 4. Go / No-Go Decision Gate (n ≥ 200 Scored Grade-A Pairs)

| Metric | Target | Benchmark Measured Value | Current Status |
|---|---|---|---|
| **Grade-A Scored Pairs (n)** | `≥ 200` | **`172`** | `ACCUMULATING LIVE SHADOW` |
| **Grade-A Mean Expectancy (R)** | `≥ +0.10 R` | **`+0.060 R`** | `MONITOR (OPTION COST THRESHOLD)` |
| **Day Win Rate (% Positive Days)** | `> 50.0%` | **`45.5%`** (5/11 days) | `MONITOR` |
| **Operating Directive** | Active Push Status | **`UNPROVEN / SHADOW TRACKING`** | — |

> [!CAUTION]
> At $n \ge 200$ grade-A pairs with verified `trade_id`, keep live desktop/push alerts ONLY if mean exit $R \ge +0.10$ on the underlying and positive on the majority of trading days. Otherwise, the weekly indicator script is treated as context only. Do NOT tune time gates or discretionary vetoes before hitting the 200-pair threshold. Live shadow stats accumulate in `skills/postmortem_live.md`.
