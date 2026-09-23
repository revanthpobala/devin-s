# Execution Timing Gates & Time-of-Day Filters

## Overview
This skill governs trade execution permissions, blackout zones, and power-hour exceptions based on time-of-day. In 0DTE options and intraday setups, timing and institutional volume participation dictate expectancy.

---

## 1. Midday Gate (11:00 ET – 14:45 ET / 09:00 MT – 12:45 MT)
- **Status:** ACTIVE MANDATORY GATE
- **Evidence (Sep 4–22 2026, engine `exit_r`):** grade-A entries 09:30–10:59 ET +0.11R (n=89; score ≥85: +0.22R, n=40); 11:00–13:59 ET 0.00R (n=73).
- **Rule 4.1:**
  - **IF** alert timestamp falls between **11:00 ET** and **14:45 ET**: take a new entry only if score $\ge 85$. Otherwise WAIT.
  - **Existing Positions:** manage by the card's stops; do not tighten them for the time of day.

---

## 2. Afternoon Index Trend (14:45 ET – 15:00 ET / 12:45 MT – 13:00 MT)
- **Status:** ACTIVE EXCEPTION
- **Rule 4.2:**
  - **IF** symbol is a major index ETF (`QQQ`, `SPY`, `IWM`, `DIA`) AND timestamp is $\ge$ **14:45 ET** and before the 15:00 ET entry cutoff:
    - **DO NOT** reject a trend signal solely due to "late session time".
    - **CONDITIONS:** the 15m trend is accelerating with expanding volume.
    - **RISK BOX:** use the card's stop. Flat by **15:45 ET (13:45 MT)**.

---

## 3. Morning Opening Drive Rules (07:30 MT – 08:00 MT / 09:30 ET – 10:00 ET)
- **Rule 4.3:**
  - First 15 minutes of market open (07:30–07:45 MT) has highest spread slippage. Wait for the 15m Opening Range (OR) high/low to be defined before taking breakout signals unless a major catalyst print (CPI, Jobs) triggers a clean directional continuation.

---

## 4. End-of-Day (EOD) Mandatory Flat Rule
- **Rule 4.4:**
  - **NO NEW 0DTE ENTRIES** after **15:00 ET (13:00 MT)**.
  - All open intraday positions MUST be closed or flat by **15:45 ET (13:45 MT)**.
  - Zero overnight holds on 0DTE options under any circumstance.