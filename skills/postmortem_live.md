# Active Session Post-Mortem Learnings & Empirical Edge

> 📊 **Empirical Performance Audit** — Generated automatically at `2026-10-05 19:45 ET` from `551` total signals in `intraday_signals`.

---

## 1. Go / No-Go Decision Gate (n ≥ 200 Pairs)

| Metric | Target | Current Measured Value | Status |
|---|---|---|---|
| **Grade-A Scored Pairs (n)** | `≥ 200` | **`29`** | `ACCUMULATING` |
| **Grade-A Mean Expectancy (R)** | `≥ +0.10 R` | **`-0.141 R`** | `MONITOR` |
| **Day Win Rate (% Positive Days)** | `> 50.0%` | **`37.5%`** (3/8 days) | `MONITOR` |
| **Operating Directive** | Active Push Status | **`INSUFFICIENT SAMPLE (29/200 pairs)`** | — |

> [!IMPORTANT]
> At n ≥ 200 grade-A pairs with `trade_id`, keep live desktop/push alerts ONLY if mean exit R is ≥ +0.10 and positive on the majority of trading days. Otherwise, the weekly indicator script is treated as context only. Do NOT tune time gates or discretionary vetoes before hitting the 200-pair threshold.

---

## 2. R Attribution by Setup Grade

| Grade | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Net Expectancy |
|---|---|---|---|---|---|---|
| **Grade A** | 87 | 29 | `-0.141 R` | 31.0% | 27.6% | 🔴 BLEED |
| **Grade B** | 5 | 1 | `+0.000 R` | 0.0% | 0.0% | 🔴 BLEED |
| **Grade C** | 59 | 7 | `-0.040 R` | 57.1% | 42.9% | 🔴 BLEED |
| **Grade D** | 4 | 4 | `-0.828 R` | 0.0% | 50.0% | 🔴 BLEED |
| **Grade UNKNOWN** | 396 | 77 | `+1.294 R` | 33.8% | 66.2% | 🟢 EDGE |

---

## 3. R Attribution by Hour of Day (ET)

| Hour (ET) | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Assessment |
|---|---|---|---|---|---|---|
| **09:00 – 09:59 ET** | 53 | 21 | `+1.555 R` | 42.9% | 28.6% | 🟢 PRIME |
| **10:00 – 10:59 ET** | 108 | 30 | `+0.418 R` | 26.7% | 60.0% | 🟢 PRIME |
| **11:00 – 11:59 ET** | 5 | 2 | `+4.581 R` | 50.0% | 50.0% | 🟢 PRIME |
| **12:00 – 12:59 ET** | 33 | 14 | `+1.996 R` | 42.9% | 57.1% | 🟢 PRIME |
| **13:00 – 13:59 ET** | 57 | 7 | `-1.250 R` | 0.0% | 100.0% | ⛔ AVOID |
| **14:00 – 14:59 ET** | 26 | 7 | `+0.400 R` | 28.6% | 71.4% | 🟢 PRIME |
| **15:00 – 15:59 ET** | 16 | 3 | `+0.550 R` | 33.3% | 66.7% | 🟢 PRIME |
| **16:00 – 16:59 ET** | 18 | 3 | `+0.806 R` | 33.3% | 66.7% | 🟢 PRIME |
| **17:00 – 17:59 ET** | 45 | 3 | `-1.250 R` | 0.0% | 100.0% | ⛔ AVOID |
| **18:00 – 18:59 ET** | 26 | 8 | `+1.340 R` | 50.0% | 50.0% | 🟢 PRIME |
| **19:00 – 19:59 ET** | 57 | 4 | `+1.372 R` | 50.0% | 50.0% | 🟢 PRIME |
| **20:00 – 20:59 ET** | 21 | 0 | `+0.000 R` | 0.0% | 0.0% | 🟡 CHOP |
| **21:00 – 21:59 ET** | 6 | 0 | `+0.000 R` | 0.0% | 0.0% | 🟡 CHOP |
| **22:00 – 22:59 ET** | 45 | 0 | `+0.000 R` | 0.0% | 0.0% | 🟡 CHOP |
| **23:00 – 23:59 ET** | 14 | 2 | `+1.337 R` | 50.0% | 50.0% | 🟢 PRIME |

---

## 4. R Attribution by Conviction Score Band

| Score Band | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % |
|---|---|---|---|---|---|
| **90+** | 59 | 6 | `-0.222 R` | 50.0% | 33.3% |
| **85-89** | 24 | 22 | `-0.126 R` | 27.3% | 27.3% |
| **80-84** | 4 | 1 | `+0.000 R` | 0.0% | 0.0% |
| **< 80** | 464 | 89 | `+1.079 R` | 33.7% | 62.9% |

---

## 5. Shadow Ledger Veto Attribution

| Veto Reason | Signals (n) | Scored (n) | Counterfactual Mean R | Win % | Capital Impact |
|---|---|---|---|---|---|
| **DAY PAUSE CIRCUIT BREAKER** | 2 | 1 | `+0.000 R` | 0.0% | 🔴 MISSED RUNNER (+0.00R blocked) |
| **GRADE VETO (NOT GRADE A)** | 58 | 1 | `+0.000 R` | 0.0% | 🔴 MISSED RUNNER (+0.00R blocked) |
| **MAX CONCURRENT EXPOSURE** | 2 | 2 | `+0.410 R` | 50.0% | 🔴 MISSED RUNNER (+0.41R blocked) |
| **NONE (EXECUTED)** | 489 | 114 | `+0.799 R` | 33.3% | EXECUTED IN REAL-TIME |
