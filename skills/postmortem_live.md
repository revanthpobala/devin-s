# Active Session Post-Mortem Learnings & Empirical Edge

> 📊 **Empirical Performance Audit** — Generated automatically at `2026-09-23 14:21 ET` from `26` total signals in `intraday_signals`.

---

## 1. Go / No-Go Decision Gate (n ≥ 200 Pairs)

| Metric | Target | Current Measured Value | Status |
|---|---|---|---|
| **Grade-A Scored Pairs (n)** | `≥ 200` | **`3`** | `ACCUMULATING` |
| **Grade-A Mean Expectancy (R)** | `≥ +0.10 R` | **`+0.000 R`** | `MONITOR` |
| **Day Win Rate (% Positive Days)** | `> 50.0%` | **`0.0%`** (0/1 days) | `MONITOR` |
| **Operating Directive** | Active Push Status | **`INSUFFICIENT SAMPLE (3/200 pairs)`** | — |

> [!IMPORTANT]
> At n ≥ 200 grade-A pairs with `trade_id`, keep live desktop/push alerts ONLY if mean exit R is ≥ +0.10 and positive on the majority of trading days. Otherwise, the weekly indicator script is treated as context only. Do NOT tune time gates or discretionary vetoes before hitting the 200-pair threshold.

---

## 2. R Attribution by Setup Grade

| Grade | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Net Expectancy |
|---|---|---|---|---|---|---|
| **Grade A** | 6 | 0 | `+0.000 R` | 0.0% | 0.0% | 🔴 BLEED |
| **Grade B** | 5 | 0 | `+0.000 R` | 0.0% | 0.0% | 🔴 BLEED |
| **Grade C** | 8 | 8 | `-0.108 R` | 37.5% | 37.5% | 🔴 BLEED |
| **Grade D** | 4 | 4 | `-1.075 R` | 0.0% | 75.0% | 🔴 BLEED |
| **Grade UNKNOWN** | 3 | 2 | `+1.577 R` | 50.0% | 50.0% | 🟢 EDGE |

---

## 3. R Attribution by Hour of Day (ET)

| Hour (ET) | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Assessment |
|---|---|---|---|---|---|---|
| **14:00 – 14:59 ET** | 5 | 3 | `+1.052 R` | 33.3% | 33.3% | 🟢 PRIME |

---

## 4. R Attribution by Conviction Score Band

| Score Band | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % |
|---|---|---|---|---|---|
| **90+** | 1 | 0 | `+0.000 R` | 0.0% | 0.0% |
| **85-89** | 1 | 0 | `+0.000 R` | 0.0% | 0.0% |
| **80-84** | 4 | 0 | `+0.000 R` | 0.0% | 0.0% |
| **< 80** | 20 | 14 | `-0.143 R` | 28.6% | 50.0% |

---

## 5. Shadow Ledger Veto Attribution

| Veto Reason | Signals (n) | Scored (n) | Counterfactual Mean R | Win % | Capital Impact |
|---|---|---|---|---|---|
| **DAY PAUSE CIRCUIT BREAKER** | 2 | 0 | `+0.000 R` | 0.0% | 🔴 MISSED RUNNER (+0.00R blocked) |
| **GRADE VETO (NOT GRADE A)** | 7 | 1 | `+0.000 R` | 0.0% | 🔴 MISSED RUNNER (+0.00R blocked) |
| **NONE (EXECUTED)** | 17 | 13 | `-0.154 R` | 30.8% | EXECUTED IN REAL-TIME |
