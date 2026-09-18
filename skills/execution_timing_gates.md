# Execution Timing Gates & Time-of-Day Filters

## Overview
This skill governs trade execution permissions, blackout zones, and power-hour exceptions based on time-of-day. In 0DTE options and intraday setups, timing and institutional volume participation dictate expectancy.

---

## 1. The Midday Low-Volume Lull Gate (11:15 MT – 12:45 MT / 13:15 ET – 14:45 ET)
- **Status:** ACTIVE MANDATORY GATE
- **Problem Statement:** Market makers and institutional algorithms extract retail premium during lunch chop. Empirically, 80%+ of breakout and reversal signals during this 90-minute window fail before reaching Target 1, leading to choppy stop-outs.
- **Rule 4.1:**
  - **IF** alert timestamp falls between **11:15 MT (13:15 ET)** and **12:45 MT (14:45 ET)**:
    - **STAND ASIDE / REJECT** all new counter-trend, reversal, or breakout signals.
    - **EXCEPTION:** Only take an entry if Conviction Score is Grade A+ ($\ge 88$) AND volume relative to the lunch slot (RVOL) is $> 1.8\times$.
    - **Existing Positions:** If already holding a trade with Target 1 hit, tighten trailing stop to break-even+ (BE+) immediately upon entering the lull.

---

## 2. Power Hour Index Trend Acceleration (14:15 MT – 15:30 MT / 16:15 ET – 17:30 ET)
- **Status:** ACTIVE EXCEPTION
- **Rule 4.2:**
  - **IF** symbol is a major index ETF (`QQQ`, `SPY`, `IWM`, `DIA`) AND timestamp is $\ge$ **14:15 MT (16:15 ET)**:
    - **DO NOT** reject trend breakdown or breakout signals solely due to "late session time".
    - **CONDITIONS:** Allow entry IF the 15-minute Stage 4 (for Puts) or Stage 2 (for Calls) is actively accelerating with expanding tick volume.
    - **RISK BOX:** Mandatory tighter stop at $0.75\times$ ATR. Close all contracts by **15:45 MT (17:45 ET)** (15 minutes prior to the closing bell). Never hold 0DTE through the final 15 minutes of RTH.

---

## 3. Morning Opening Drive Rules (07:30 MT – 08:00 MT / 09:30 ET – 10:00 ET)
- **Rule 4.3:**
  - First 15 minutes of market open (07:30–07:45 MT) has highest spread slippage. Wait for the 15m Opening Range (OR) high/low to be defined before taking breakout signals unless a major catalyst print (CPI, Jobs) triggers a clean directional continuation.

---

## 4. End-of-Day (EOD) Mandatory Flat Rule
- **Rule 4.4:**
  - **NO NEW 0DTE ENTRIES** after **15:00 MT (17:00 ET)**.
  - All open intraday positions MUST be closed or flat by **15:45 MT (17:45 ET)**.
  - Zero overnight holds on 0DTE options under any circumstance.
