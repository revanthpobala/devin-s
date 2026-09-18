# Options Spread Architect & Volatility Playbook

## Overview
This skill guides the selection of option structures (long naked calls/puts, vertical debit spreads, credit spreads, calendar spreads) based on volatility regime, IV Rank, and Expected Move (EM).

---

## 1. Volatility Regime Mapping (Tastytrade Metrics)
- **IV Rank < 30 (Cheap Volatility):**
  - **Structure:** Long Single-Leg Calls/Puts or Tight Vertical Debit Spreads (e.g. Bull Call Spread, Bear Put Spread).
  - **Rationale:** Volatility is cheap; buying extrinsic value has high asymmetric upside if the underlying travels.
- **IV Rank 30 – 50 (Fair Volatility):**
  - **Structure:** 50/50 Debit or Defined-Risk Spreads. Target delta 0.40–0.50 on long leg, sell 0.20–0.25 delta on short leg.
- **IV Rank > 50 (Rich Volatility / High Premium):**
  - **Structure:** Defined-Risk Credit Spreads (Bull Put Spread / Bear Call Spread) or Wide Debit Spreads where the short leg finances 40%+ of the long leg.
  - **Rationale:** High IV crush risk. Avoid long single-leg calls/puts because theta and vega decay crush directional gains.

---

## 2. Expected Move (EM) Alignment
- **Rule 3.1:**
  - On 0DTE/1DTE options, Target 1 MUST sit inside the session Expected Move (`EM ±x%`).
  - Proposing a Target 1 beyond the daily Expected Move is statistically aggressive and rejected as low-probability.

---

## 3. Strike Selection Geometry
- **Rule 3.2:**
  - **Intraday Momentum Scalp (0DTE):** ATM strike or 1-strike ITM (delta 0.50–0.60) for high gamma responsiveness and minimal theta slippage. Never buy deep OTM lotto contracts (delta < 0.25) unless specified as lotto size.
  - **Tactical Swing (14–45 DTE):** ATM long strike paired with short strike at Target 1 / resistance level. Maximize R:R $\ge 2.5:1$.
