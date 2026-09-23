# Catastrophe Risk Controls & Dynamic Stop Discipline

## Overview
This skill governs hard capital protection rules, stop loss geometry, and catastrophe circuit breakers to prevent outsized tail-risk drawdowns on intraday and swing options.

---

## 1. The Hard ATR Loss-Cap Circuit Breaker
- **Rule 2.4:**
  - **Maximum Allowed Initial Stop Distance:** $1.25 \times \text{5m ATR}$ (for intraday 0DTE) or $1.50 \times \text{Daily ATR}$ (for swing trades).
  - **Empirical Rationale:** In the 2026-09-16 post-mortem, runaway losses on GOOGL (-$185) and TSLA (-$159) occurred when initial stop brackets drifted past $1.5\times$ ATR before cutting.
  - **Action Required:**
    - If the technical invalidation level requires a stop greater than $1.25\times$ ATR:
      1. EITHER reduce position sizing (e.g. choose lower delta contract or cut contract count in half).
      2. OR reject the setup completely as **"R:R Distorted / Stop Too Wide"**.

---

## 2. Dynamic Trailing Stop Discipline
- **Rule 2.5:**
  - **Target 1 Reached:** Scale 50% of position size. Move the stop on remaining contracts to **Break-Even + 0.05 pts (BE+)** immediately.
  - **Target 2 / Peak ≥ 2.0×ATR:** Trail runner to protect ≥65% of peak gains. Remaining position rides with trailing stop until trend invalidation.
  - **Runner Management:** Let remaining 25% ride with trailing 5-period EMA or previous bar low/high until trend invalidation.
  - **Never turn a winning trade that hit Target 1 into a loss.**

---

## 3. Daily Loss Limit & Account Circuit Breaker
- **Rule 2.6:**
  - **Max Consecutive Losses:** After 3 consecutive intraday losses in a single session, the system enters a mandatory **DAY PAUSE**.
  - **Auto-Clearing Mechanism:** The pause clears ONLY if a Grade A+ ($\ge 88$) setup fires with fresh macro catalyst tailwinds, or when the next session opens.
  - **Slippage Cap:** If market spread exceeds 8% of the option bid/ask, market orders are strictly forbidden; use limit orders only.
