# Active Session Post-Mortem Learnings & Empirical Edge

> 📊 **Empirical Performance Audit** — Generated automatically at `2026-09-23 13:42 ET` from `14` total signals in `intraday_signals`.

---

## 1. Go / No-Go Decision Gate (n ≥ 200 Pairs)

| Metric | Target | Current Measured Value | Status |
|---|---|---|---|
| **Grade-A Scored Pairs (n)** | `≥ 200` | **`2`** | `ACCUMULATING` |
| **Grade-A Mean Expectancy (R)** | `≥ +0.10 R` | **`+0.000 R`** | `MONITOR` |
| **Day Win Rate (% Positive Days)** | `> 50.0%` | **`0.0%`** (0/1 days) | `MONITOR` |
| **Operating Directive** | Active Push Status | **`INSUFFICIENT SAMPLE (2/200 pairs)`** | — |

> [!IMPORTANT]
> At n ≥ 200 grade-A pairs with `trade_id`, keep live desktop/push alerts ONLY if mean exit R is ≥ +0.10 and positive on the majority of trading days. Otherwise, the weekly indicator script is treated as context only. Do NOT tune time gates or discretionary vetoes before hitting the 200-pair threshold.

---

## 2. R Attribution by Setup Grade

| Grade | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Net Expectancy |
|---|---|---|---|---|---|---|
| **Grade A** | 4 | 0 | `+0.000 R` | 0.0% | 0.0% | 🔴 BLEED |
| **Grade B** | 5 | 0 | `+0.000 R` | 0.0% | 0.0% | 🔴 BLEED |
| **Grade C** | 4 | 4 | `+1.125 R` | 100.0% | 0.0% | 🟢 EDGE |
| **Grade D** | 1 | 1 | `-0.450 R` | 0.0% | 0.0% | 🔴 BLEED |

---

## 3. R Attribution by Hour of Day (ET)

| Hour (ET) | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Assessment |
|---|---|---|---|---|---|---|

---

## 4. R Attribution by Conviction Score Band

| Score Band | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % |
|---|---|---|---|---|---|
| **80-84** | 4 | 0 | `+0.000 R` | 0.0% | 0.0% |
| **< 80** | 10 | 5 | `+0.810 R` | 80.0% | 0.0% |

---

## 5. Shadow Ledger Veto Attribution

| Veto Reason | Signals (n) | Scored (n) | Counterfactual Mean R | Win % | Capital Impact |
|---|---|---|---|---|---|
| **DAY PAUSE CIRCUIT BREAKER** | 2 | 0 | `+0.000 R` | 0.0% | 🔴 MISSED RUNNER (+0.00R blocked) |
| **GRADE VETO (NOT GRADE A)** | 6 | 0 | `+0.000 R` | 0.0% | 🔴 MISSED RUNNER (+0.00R blocked) |
| **NONE (EXECUTED)** | 6 | 5 | `+0.810 R` | 80.0% | EXECUTED IN REAL-TIME |
