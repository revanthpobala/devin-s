"""
INCOME tier and the after-close digest.

INCOME fires on held shares or CSP-eligible names that have run extended: Ext Z >= 2.0 in a
regime that is not a climax. Three constraints are load-bearing:

  * It is labelled a TOUCH PROBABILITY, never an expectancy. There is no options price data in
    this pipeline, so "this makes money" is not a claim that can be supported here.
  * Regime 2 is a hard "do NOT sell calls". Climax drifts +3.1% relative after the call is sold.
  * A post-crash regime (the index >15% off its high) needs a 5-10 bar expiry: touching a strike
    inside a dead-cat bounce is the base case, not an exception.

The digest carries everything deliberately excluded from push: non-PB onsets, stalking names that
reached their zone, the Code-20 secondary lane, the watch list ranked by measured R:R with stop
width, and the scoreboard delta.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.logic.data_window_filter import decode_action
from src.tracking.entry_risk_alerts import (
    EXT_Z_TRIM,
    Onset,
    _num,
    fire_entry,
    load_onsets_for_date,
)
from src.tracking.notify import is_digest_time, notify

logger = logging.getLogger(__name__)

# Measured in the corpus: P(touch within +1x Exp Move over 21 bars).
TOUCH_PROB_WITH_EXT_Z_GATE = 0.216
TOUCH_PROB_BASELINE = 0.382

# Short strikes at the Short Stop Loss were hit 63-73% of the time.
SHORT_STOP_LOSS_HIT_RATE = (0.63, 0.73)

# Climax drift after selling the call.
CLIMAX_RELATIVE_DRIFT_PCT = 3.1

POST_CRASH_DRAWDOWN_PCT = 15.0
POST_CRASH_MIN_BARS = 5
POST_CRASH_MAX_BARS = 10

CLIMAX_REGIME = 2
STRIKE_EXP_MOVE_MULTIPLE = 1.0


@dataclass
class IncomeSignal:
    ticker: str
    ext_z: float
    regime: Optional[int]
    close: Optional[float]
    exp_move_pct: Optional[float]
    index_drawdown_pct: Optional[float]
    strike: Optional[float] = None
    bars_to_expiry: Optional[int] = None
    blocked: bool = False
    reasons: List[str] = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []

    @property
    def is_climax(self) -> bool:
        return self.regime == CLIMAX_REGIME

    @property
    def is_post_crash(self) -> bool:
        return (self.index_drawdown_pct or 0.0) >= POST_CRASH_DRAWDOWN_PCT

    @property
    def expiry_window(self) -> str:
        if self.is_post_crash:
            return f"{POST_CRASH_MIN_BARS}-{POST_CRASH_MAX_BARS} bars (post-crash)"
        return "21 bars (standard)"


def covered_call_candidate(o: Onset, index_drawdown_pct: Optional[float] = None) -> Optional[IncomeSignal]:
    """Build an INCOME signal for an extended held name, or None when it is not a candidate.

    The strike is set at >= 1x Exp Move and is explicitly NOT the Short Stop Loss: a strike parked
    at the invalidation level was hit 63-73% of the time, which is the same as selling at the stop.
    """
    if o.ext_z is None or o.ext_z < EXT_Z_TRIM:
        return None

    sig = IncomeSignal(
        ticker=o.ticker,
        ext_z=o.ext_z,
        regime=o.regime,
        close=o.close,
        exp_move_pct=o.exp_move_pct,
        index_drawdown_pct=index_drawdown_pct,
    )

    if sig.is_climax:
        sig.blocked = True
        sig.reasons.append(
            f"Regime 2 (climax): do NOT sell calls. Calls sold into a climax drifted "
            f"{CLIMAX_RELATIVE_DRIFT_PCT:+.1f}% relative afterwards."
        )
        return sig

    if o.close and o.exp_move_pct:
        sig.strike = round(o.close * (1 + STRIKE_EXP_MOVE_MULTIPLE * o.exp_move_pct / 100.0), 2)
    sig.bars_to_expiry = POST_CRASH_MIN_BARS if sig.is_post_crash else 21

    sig.reasons.append(
        f"Ext Z {o.ext_z:.2f} >= {EXT_Z_TRIM}: extension is where premium is richest"
    )
    if sig.strike:
        sig.reasons.append(
            f"strike >= {STRIKE_EXP_MOVE_MULTIPLE:.0f}x Exp Move -> ${sig.strike:.2f} "
            f"(1x EM = {o.exp_move_pct:.2f}%)"
        )
        sig.reasons.append(
            f"do NOT place the strike at the Short Stop Loss (hit "
            f"{SHORT_STOP_LOSS_HIT_RATE[0]:.0%}-{SHORT_STOP_LOSS_HIT_RATE[1]:.0%})"
        )
    if sig.is_post_crash:
        sig.reasons.append(
            f"index is {sig.index_drawdown_pct:.0f}% off its high: use a "
            f"{POST_CRASH_MIN_BARS}-{POST_CRASH_MAX_BARS} bar expiry, not a monthly"
        )
    return sig


def income_body(sig: IncomeSignal) -> str:
    lines = [f"**{sig.ticker}** · covered-call candidate", ""]
    if sig.blocked:
        lines.extend(sig.reasons)
        lines.append("")
        lines.append("_No strike suggested: this is a do-not-sell condition._")
        return "\n".join(lines)

    lines.extend([
        f"- Ext Z: {sig.ext_z:.2f} · Regime: {sig.regime if sig.regime is not None else '?'}"
        f" · expiry: {sig.expiry_window}",
        f"- P(touch within +1x Exp Move, 21 bars): **{TOUCH_PROB_WITH_EXT_Z_GATE:.1%}**"
        f" vs {TOUCH_PROB_BASELINE:.1%} baseline — so this gate is an EXCLUSION, not an edge",
        "",
    ])
    lines.extend(f"- {r}" for r in sig.reasons)
    lines.extend([
        "",
        "**This is a touch probability, not an expectancy.** No options price data exists in "
        "this pipeline, so the premium collected and the downside if it is touched are both "
        "unmeasured here.",
    ])
    return "\n".join(lines)


def fire_income(signals: List[IncomeSignal], date: str, force: bool = False) -> List[str]:
    """Push each INCOME signal once per (tier, ticker, date).

    force=True is used only by run_digest. The digest is emitted after the close, which is by
    definition outside the interactive push window; INCOME findings computed from the same settled
    bar ship with it rather than being silently dropped. A mid-session INCOME call keeps the normal
    quiet-hours rule, so a 3 AM sweep still cannot page the phone.
    """
    pushed: List[str] = []
    for sig in signals:
        title = (f"{sig.ticker} DO NOT sell calls (climax)" if sig.blocked
                 else f"{sig.ticker} covered-call candidate")
        if notify("INCOME", title, income_body(sig),
                  dedupe_key=f"INCOME:{sig.ticker}:{date}", force=force):
            pushed.append(sig.ticker)
    return pushed


def collect_income(
    onsets: List[Onset],
    index_drawdown_pct: Optional[float] = None,
    holdings: Optional[List[str]] = None,
) -> List[IncomeSignal]:
    """Collect covered call candidates for held positions only."""
    holdings_set = {h.upper() for h in (holdings or []) if h}
    return [
        s for s in (covered_call_candidate(o, index_drawdown_pct) for o in onsets if o.ticker in holdings_set)
        if s is not None
    ]


# =====================================================================================
# DIGEST
# =====================================================================================

CODE20_REVERSAL_ACTION_CODE = 20


@dataclass
class DigestSection:
    title: str
    lines: List[str]


def build_digest(
    date: str,
    onsets: List[Onset],
    entry_digest_candidates: Optional[List[Dict[str, Any]]] = None,
    code20_on_action: Optional[List[str]] = None,
    holdings: Optional[List[str]] = None,
    scoreboard_delta: Optional[str] = None,
) -> str:
    """The after-close summary. Everything here is a deliberate non-push."""
    holdings_set = {h.upper() for h in (holdings or [])}
    sections: List[DigestSection] = []

    # 1. Non-PB R:R onsets -- the whole reason a digest exists.
    non_pb = [c for c in (entry_digest_candidates or []) if not c.get("pb")]
    if non_pb:
        sections.append(DigestSection("Non-PB R:R onsets (measured flat/negative -> no push)", [
            f"- {c['ticker']} RR {c.get('rr_at_market')} · stop "
            f"{c.get('stop_width_atr')} ATR · {'; '.join(c.get('reasons', []))}"
            for c in non_pb
        ]))

    # 2. Stalking names that reached their zone.
    in_zone = [
        o for o in onsets
        if o.packs_present and o.in_zone and o.rr_ok and o.rr_at_market is not None
        and o.ticker in holdings_set
    ]
    if in_zone:
        sections.append(DigestSection("Held names now in zone", [
            f"- {o.ticker} RR {_num(o.rr_at_market, '.1f')} · stop {_num(o.stop_width_atr)} ATR"
            for o in sorted(in_zone, key=lambda x: -(x.rr_at_market or 0))
        ]))

    # 3. Code 20 REVERSAL -- a secondary lane, +0.05R. Never a push.
    rev = [o for o in onsets if o.action_code == CODE20_REVERSAL_ACTION_CODE]
    rev_tickers = [t for t in (code20_on_action or [])] or [o.ticker for o in rev]
    if rev_tickers:
        sections.append(DigestSection(
            "Code 20 REVERSAL BUY (secondary lane, ~+0.05R -- no push)", [
                f"- {t} ({decode_action(CODE20_REVERSAL_ACTION_CODE)})" for t in rev_tickers
            ]
        ))

    # 4. Watch list. Sorting by raw R:R alone is the defect this whole tier exists to prevent: a
    # 0.02-ATR stop reports RR 250 and takes the top slot while the ratio means nothing. Rows whose
    # stop is inside noise are therefore demoted BELOW every row with a measurable stop, and R:R
    # only orders rows that are actually comparable.
    watchable = [o for o in onsets if o.packs_present and o.rr_at_market is not None]
    watchable.sort(key=lambda o: (o.stop_tight, -(o.rr_at_market or 0)))
    if watchable:
        sections.append(DigestSection("Watch list by RR@mkt (stop width matters more than the ratio)", [
            f"- {o.ticker} RR {_num(o.rr_at_market, '.1f')}"
            + (f" ⚠️ {_num(o.stop_width_atr)} ATR inside noise — ratio not comparable"
               if o.stop_tight
               else f" · {_num(o.stop_width_atr)} ATR")
            + (f" · {o.lane}" if o.lane else "")
            for o in watchable[:25]
        ]))

    # 5. Scoreboard delta.
    sections.append(DigestSection("Scoreboard delta", [f"- {scoreboard_delta or 'no change'}"]))

    header = [f"**Daily digest · {date} MT**", ""]
    if not sections[:-1]:
        header.append("_No non-PB onsets, no in-zone held names, no code-20. Quiet session._")
        header.append("")
    for s in sections:
        header.append(f"### {s.title}")
        header.extend(s.lines)
        header.append("")
    return "\n".join(header)


def run_digest(
    date: Optional[str] = None,
    holdings: Optional[List[str]] = None,
    scoreboard_delta: Optional[str] = None,
    notify_enabled: bool = True,
) -> Dict[str, Any]:
    """Emit the digest and the INCOME tier together. Both are after-close work."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    onsets = load_onsets_for_date(date)

    entry = fire_entry(onsets, date) if notify_enabled else {
        "pushed": [], "digest_candidates": [], "checked": len(onsets),
    }

    if holdings is None:
        try:
            from src.tracking.position_state import load_positions
            pos_data = load_positions()
            if isinstance(pos_data, dict):
                open_pos = pos_data.get("open_positions", pos_data)
                if isinstance(open_pos, dict):
                    holdings = list(open_pos.keys())
                elif isinstance(open_pos, list):
                    holdings = [p.get("ticker", "") for p in open_pos if isinstance(p, dict)]
        except Exception as e:
            logger.warning(f"Could not load holdings for income digest: {e}")
            holdings = []

    income_signals = collect_income(onsets, holdings=holdings)
    # Only deliver out-of-window when the digest itself is actually due.
    after_close = is_digest_time()
    income_pushed = fire_income(income_signals, date, force=after_close) if notify_enabled else []

    body = build_digest(
        date=date,
        onsets=onsets,
        entry_digest_candidates=entry["digest_candidates"],
        holdings=holdings,
        scoreboard_delta=scoreboard_delta,
    )
    digest_sent = notify("DIGEST", f"Daily digest {date}", body,
                         dedupe_key=f"DIGEST:{date}") if notify_enabled else False

    return {
        "date": date,
        "onsets_scanned": len(onsets),
        "entry_pushed": entry["pushed"],
        "non_pb_onsets": [c["ticker"] for c in entry["digest_candidates"] if not c.get("pb")],
        "income_candidates": [s.ticker for s in income_signals],
        "income_blocked_climax": [s.ticker for s in income_signals if s.blocked],
        "income_pushed": income_pushed,
        "digest_sent": digest_sent,
        "after_close": after_close,
        "digest_body": body,
    }