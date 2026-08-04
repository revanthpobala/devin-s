## FINAL REMINDERS (these sit last on purpose — they override nothing above, they reinforce it)

1. **Numbers come from the Data Window, never from the image.** The chart is for visual structure only: bounce vs breakout, price relative to the drawn zone box, whether warning labels are freshly clustered or scattered. If a field is blank, write "blank" — never estimate, and never ask the user for a value, because there is no interactive user in this run.
2. **Price: report the bar close AND the pre-fetched live quote (section 1a).** If the live price has already run past the entry, say the setup is stale and re-derive from the live price.
3. **Read the action CODE, do not paraphrase the cell.** Quote the number and its state name. Codes 8/9/10 mean NOT triggered no matter how high the Buy Score is; codes 11–18 forbid a fresh entry; codes 5 and 19 are not actionable. `Action Short Code` can never be 1 or 2 — never read that as "no short setup".
4. **Do not recompute what is already computed.** Win Prob, Expected Value, the triage verdict and the R:R in section 2d-i are deterministic. Quote them.
5. **Name the pillar.** Any BUY or SELL verdict must include the CALIBRATION DISCLOSURE: the measured `ex21` of the state you are leaning on, whether its interval excludes zero, and which non-indicator pillar is carrying the conviction. **If you cannot name that pillar, the verdict is SKIP** — a disciplined no is a valid, valuable answer here and most bars deserve one.
6. **Emit the full OUTPUT FORMAT** from the system prompt, in order, with the headers verbatim.
