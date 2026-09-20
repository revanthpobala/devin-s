## FINAL REMINDERS (these sit last on purpose — they override nothing above, they reinforce it)

1. **Numbers come from the Data Window, never from the image.** The chart is for visual structure only: base formation, support defense, price relative to drawn levels, and label clustering. If a field is blank, write "blank" — never estimate or hallucinate.
2. **Price: report the bar close AND the pre-fetched live quote (section 1a).** If the live price has moved past an initial level, re-derive the tactical plan from the live price.
3. **Multi-Perspective Synthesis Over Mechanical Vetoes:**
   - Code 20 (REVERSAL BUY) is the sole active indicator reversal trigger (Codes 1 and 2 are retired, pine:6180-6184).
   - Codes 8 and 10 are **Staging & Base-Building States**. While mechanical momentum has not auto-fired yet, **you MAY authorize an Actionable Floor Defense Entry (Plan A), Bull Put Credit Spread (Plan B), or Deep ITM LEAPS** when price is defended by an indisputable floor (Pin Bars, Put Wall, 20 EMA, Gap Retest) and powered by verified Fundamental Catalysts (Pillar 3). Anchor the tactical stop tightly beneath the defended floor.
   - Codes 11–18 forbid fresh direct entries due to exhaustion/parabolic danger.
4. **Do not recompute what is already computed.** Win Prob, Expected Value, and Data Window fields are deterministic ground truths. Quote them literally.
5. **Name the Pillars in your Calibration Disclosure:**
   - If taking an indicator-based entry (Code 20), cite its measured interval (+0.038R, 4/4 eras, win 49.5%).
   - If taking a **Floor Defense Setup (during Code 8/10)**, explicitly disclose that the trade is carried by Pillar 2 (Floor Defense / Rejection Wicks) + Pillar 3 (Fundamental Catalysts), anchored to the local structural stop.
   - Always emit the complete **Multi-Regime Action Plan (Plan A, Plan B, Plan C, Plan D)**.
6. **Emit the full OUTPUT FORMAT** from the system prompt, in order, with the headers verbatim.
7. **DRAW THE ASCII ART.** You MUST explicitly draw the ASCII diagram in THE SETUP section showing Target, Resistance, Live Spot, Local Base Floor, and Local Stop. Do not skip it.
8. **Options Horizon & Structure Evaluation in Plan B (Tactical vs. Multi-Quarter / LEAPS):**
   - The LLM should evaluate whether the stock's regime, IV Rank, and fundamental outlook favor a tactical swing, a multi-quarter / LEAPS structure, or selling credit / skipping:
     - **Plan B-1: Tactical Swing (21–45 DTE)**: Defined-risk vertical spreads (e.g. Bull Call / Bull Put) capturing near-term technical levels or support defense.
     - **Plan B-2: Multi-Quarter / LEAPS (120–500+ DTE)**: If fundamental compounding and long-term re-rating warrant a multi-quarter horizon, evaluate Deep ITM Calls (Delta 0.70–0.85) or Long Diagonals. If the setup does not justify buying multi-year premium (e.g. extreme IV Rank >80 or Stage 4 distribution where credit selling or skipping is superior), state why clearly.
9. **RSI2 Mean Reversion Setup Parity & Fixed Level Preservation:**
   - If analyzing an RSI2 setup, its levels (`visibleEntry`, `visibleStop`, `visibleTarget`, `opening_ceiling`) are mathematically fixed. **Do NOT silently overwrite or mutate fixed RSI2 exits** with generic Plan A discretionary trailing stops.
   - **Single Next-Open Rule**: RSI2 entries execute exclusively at the immediate next open. If the stock opened above `opening_ceiling` on that bar, the setup is **EXPIRED_CEILING**; do not recommend chasing at market.
   - **Recovery Exit**: If price closes above the 5-day EMA, an EMA5 recovery exit is queued for the following bar's open.
10. **Handling Unknown Earnings & Missing Option Data:**
   - If next earnings date is unknown, explicitly state "Earnings Date: UNKNOWN / VERIFY" rather than inventing a date.
   - If live options chains are unavailable, evaluate delta/strike geometry based on synthetic historical pricing or recommend equity shares (Plan A); never fabricate bid/ask option quotes.
11. **Report-Level Extraction Integrity:**
   - Always preserve the standard structured section headers and tactical table rows (`Entry Zone`, `Stop Loss`, `Target 1`, `Target 2`, `Tactical R:R`, `Mathematical R:R`) so downstream parsers and watch alert engines extract valid numbers without fallback regex errors.
