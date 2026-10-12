"""
src/ui/services/action_engine.py

Deterministic Actionability Layer for the Today desk.

Turns the pipeline's raw streams (opportunities, regime, holdings, live book, closed trades)
into the four things a trader actually needs in the first five minutes:

  1. `get_bat_signal()`      -> one line: what to do now, and why (counts + posture).
  2. `get_risk_flags()`      -> per-setup disqualifiers: already held, earnings blackout,
                                correlation cluster, chasing the zone.
  3. `get_event_radar()`     -> drastic movers joined to *why* they moved and what they do
                                to the user's actual book.
  4. `get_calibration_panel()` -> what has historically worked for THIS user (win rate,
                                avg R, expectancy) so setups can be ranked by proven edge,
                                not just by conviction score.

Everything here is deterministic and failure-tolerant: if a feed is down, the section is
simply omitted rather than blocking the desk. No LLM calls, no cost.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.ui.state import get_db as _get_db

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {}
_CACHE_T: float = 0.0
_TTL: float = 30.0

_EARNINGS_BLACKOUT_DAYS = 5
_CLUSTER_MIN = 3

# Sector / theme clusters. Only used to answer "am I just buying the same bet three times?"
_CLUSTERS: Dict[str, List[str]] = {
    "AI_SEMIS": ["NVDA", "AMD", "AVGO", "MRVL", "MU", "TSM", "INTC", "ARM", "LSCC", "CRDO", "COHR", "AAOI"],
    "AI_NETWORKING": ["ANET", "CSCO", "JNPR", "CIEN", "DELL", "SMCI", "VRT", "NVT"],
    "BIG_TECH": ["AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NFLX", "TSLA"],
    "CLOUD_SOFTWARE": ["CRM", "NOW", "ADBE", "ORCL", "SAP", "SNOW", "MDB", "DDOG", "NET", "PLTR"],
    "CYBER": ["CRWD", "PANW", "ZS", "FTNT", "OKTA", "S", "CYBR"],
    "ENERGY": ["XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC", "PSX", "VLO", "FANG"],
    "FINANCIALS": ["JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "BLK", "BX", "KRE"],
    "BIOTECH": ["AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA", "NVO", "LLY", "ALNY", "SRPT"],
    "CHINA_INTERNET": ["BABA", "PDD", "JD", "BIDU", "NTES", "TCEHY"],
    "INDEX_ETFS": ["SPY", "QQQ", "IWM", "DIA", "MDY", "NDX", "RSP"],
}
_TICKER_TO_CLUSTER: Dict[str, str] = {}
for _c, _members in _CLUSTERS.items():
    for _m in _members:
        _TICKER_TO_CLUSTER[_m] = _c


# ─────────────────────────────────────────────────────────────────────────────
# 1. BAT SIGNAL — the one line
# ─────────────────────────────────────────────────────────────────────────────

def _posture(regime: Dict[str, Any], in_zone: List[Dict[str, Any]],
             n_held_in_zone: int, n_blackout: int) -> Dict[str, str]:
    tide = (regime.get("tide") or {})
    fg = (regime.get("fear_greed") or {})
    vix = (regime.get("vix") or {})
    bullish = bool(tide.get("is_bullish"))
    fg_score = float(fg.get("score") or 50.0)
    vix_px = float(vix.get("price") or 20.0)

    if not in_zone:
        return {
            "action": "WAIT",
            "tone": "amber",
            "why": "No setup inside its entry zone right now — nothing is triggerable, so the correct action is to do nothing and let the alerts come to you.",
            "size": "0% deployment",
        }
    if not bullish and in_zone:
        return {
            "action": "HALF SIZE",
            "tone": "amber",
            "why": f"Market tide is {tide.get('trend', 'DEFENSIVE')}, so in-zone setups get half size — the setup is valid but the tape is fighting it.",
            "size": "50% of normal position size",
        }
    if n_held_in_zone:
        return {
            "action": "MANAGE, DON'T ADD",
            "tone": "cyan",
            "why": f"{n_held_in_zone} of the in-zone setups are names you already hold — the actionable work is managing those legs (covered call / trim / roll), not opening new risk.",
            "size": "redeploy premium rather than new capital",
        }
    if fg_score >= 75:
        return {
            "action": "HARVEST / HEDGE",
            "tone": "rose",
            "why": f"Fear & Greed is {fg_score:.0f} (extreme greed) — top-side complacency. Take premium on strength and protect winners instead of chasing entries.",
            "size": "sell premium, no new debit risk",
        }
    if fg_score <= 25:
        return {
            "action": "DEPLOY INTO QUALITY",
            "tone": "green",
            "why": f"Fear & Greed is {fg_score:.0f} (extreme fear) with a bullish tide — the asymmetric window. Scale into the in-zone setups.",
            "size": "up to 60% of dry powder",
        }
    if vix_px < 15:
        return {
            "action": "BUY DEBIT, NOT CREDIT",
            "tone": "green",
            "why": f"VIX {vix_px:.2f} is complacent and the tide is bullish — volatility is cheap, so defined-risk debit structures have the better expected value.",
            "size": "25-50% of dry powder",
        }
    return {
        "action": "DEPLOY IN ZONE",
        "tone": "green",
        "why": f"Tide {tide.get('trend', 'BULLISH')} with {len(in_zone)} in-zone trigger(s) and no regime contradiction — take the candles that are actually in their zone.",
        "size": "25-50% of dry powder",
    }


def get_bat_signal(opportunities: Optional[List[Dict[str, Any]]] = None,
                   regime: Optional[Dict[str, Any]] = None,
                   open_positions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One deterministic directive: what to do now, with the counts that justify it."""
    opportunities = opportunities or []
    regime = regime or {}
    open_positions = open_positions or []

    try:
        holdings = _holdings_by_underlying()
    except Exception as e:
        logger.debug(f"Bat signal holdings failed: {e}")
        holdings = {}

    in_zone = [o for o in opportunities if o.get("in_zone")]
    high_conv = [o for o in opportunities if (o.get("conviction") or 0) >= 7]
    coiling = [o for o in opportunities if not o.get("in_zone") and (o.get("priority_tier") or 9) <= 2]
    held = {t.upper() for t in holdings.keys()}

    held_in_zone = [o for o in in_zone if (o.get("ticker") or "").upper() in held]

    # Earnings blackout scan is the expensive part (network per ticker); cap and cache it.
    blackout = []
    for o in (in_zone + coiling)[:6]:
        sym = (o.get("ticker") or "").upper()
        if not sym:
            continue
        days = _days_to_earnings(sym)
        if days is not None and days <= _EARNINGS_BLACKOUT_DAYS:
            blackout.append({"ticker": sym, "days": days})

    posture = _posture(regime, in_zone, len(held_in_zone), len(blackout))

    counts = {
        "actionable": len(opportunities),
        "in_zone": len(in_zone),
        "high_conviction": len(high_conv),
        "coiling": len(coiling),
        "held_in_zone": len(held_in_zone),
        "open_positions": len(open_positions),
        "earnings_blackout": len(blackout),
    }

    parts = []
    if counts["in_zone"]:
        parts.append(f"{counts['in_zone']} in zone")
    if counts["coiling"]:
        parts.append(f"{counts['coiling']} coiling")
    if counts["held_in_zone"]:
        parts.append(f"{counts['held_in_zone']} held & in zone")
    if counts["earnings_blackout"]:
        parts.append(f"{counts['earnings_blackout']} inside earnings blackout")
    if counts["open_positions"]:
        parts.append(f"{counts['open_positions']} open positions monitored")

    headline = " · ".join(parts) if parts else "no triggerable setups on the board"

    return {
        "action": posture["action"],
        "tone": posture["tone"],
        "why": posture["why"],
        "size": posture["size"],
        "headline": headline,
        "counts": counts,
        "in_zone": [(o.get("ticker") or "").upper() for o in in_zone][:8],
        "held_in_zone": [(o.get("ticker") or "").upper() for o in held_in_zone][:8],
        "blackouts": blackout,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. RISK FLAGS — per-setup disqualifiers
# ─────────────────────────────────────────────────────────────────────────────

def get_risk_flags(opportunities: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Per-ticker risk flags that change whether a setup is actually takeable."""
    opportunities = opportunities or []
    try:
        holdings = _holdings_by_underlying()
    except Exception as e:
        logger.debug(f"Risk flags holdings failed: {e}")
        holdings = {}

    held = {t.upper() for t in holdings.keys()}
    clusters: Dict[str, List[str]] = {}
    for t in held:
        c = _TICKER_TO_CLUSTER.get(t)
        if c:
            clusters.setdefault(c, []).append(t)

    out: Dict[str, Any] = {}
    for o in opportunities:
        sym = (o.get("ticker") or "").upper()
        if not sym:
            continue
        flags: List[Dict[str, Any]] = []

        if sym in held:
            qty = holdings.get(sym, {}).get("quantity")
            unit = "ct" if (holdings.get(sym, {}).get("asset_type") == "OPTION") else "sh"
            flags.append({
                "code": "HELD",
                "tone": "cyan",
                "label": f"ALREADY HELD ({abs(float(qty or 0)):g}{unit})",
                "note": "Adding here increases concentration instead of taking a fresh setup — consider managing the existing leg (covered call / trim / roll).",
            })

        days = _days_to_earnings(sym)
        if days is not None and days <= _EARNINGS_BLACKOUT_DAYS:
            flags.append({
                "code": "EARNINGS",
                "tone": "rose",
                "label": f"EARNINGS IN {days}d",
                "note": f"Binary event inside {_EARNINGS_BLACKOUT_DAYS} days — a defined-risk structure or a wait avoids the gap risk.",
            })

        cluster = _TICKER_TO_CLUSTER.get(sym)
        if cluster and held:
            overlap = [t for t in clusters.get(cluster, []) if t != sym]
            if len(overlap) + (1 if sym in held else 0) >= _CLUSTER_MIN:
                flags.append({
                    "code": "CLUSTER",
                    "tone": "amber",
                    "label": f"CLUSTER {cluster.replace('_', ' ')}",
                    "note": f"You already carry {len(overlap) + 1} names in {cluster.replace('_', ' ').title()} ({', '.join(sorted(set([sym] + overlap))[:5])}) — this is one bet, not independent diversification.",
                })

        if not o.get("in_zone") and (o.get("dist_pct") or 0) > 3:
            flags.append({
                "code": "CHASING",
                "tone": "amber",
                "label": f"+{(o.get('dist_pct') or 0):.1f}% ABOVE ZONE",
                "note": "Price has already run past the entry zone — the edge was in the zone, not above it.",
            })

        live_rr = o.get("live_rr") or 0
        if 0 < live_rr < 1.2:
            flags.append({
                "code": "WEAK_RR",
                "tone": "amber",
                "label": f"R:R {live_rr:.2f}:1",
                "note": "Sub-1.2 R:R at current spot — the structure needs to widen (further expiry or tighter stop) before it pays.",
            })

        if flags:
            out[sym] = {"flags": flags, "severity": max(1 if f["tone"] == "cyan" else 2 for f in flags)}

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. EVENT RADAR — drastic movers, why they moved, what they do to your book
# ─────────────────────────────────────────────────────────────────────────────

def get_event_radar(movers: Optional[List[Dict[str, Any]]] = None,
                    opportunities: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Join each drastic mover to its cause and to the user's real exposure."""
    movers = movers or []
    opportunities = opportunities or []
    if not movers:
        return []

    try:
        holdings = _holdings_by_underlying()
    except Exception as e:
        logger.debug(f"Event radar holdings failed: {e}")
        holdings = {}

    held = {t.upper() for t in holdings.keys()}
    on_board = {(o.get("ticker") or "").upper() for o in opportunities}
    radical = [m for m in movers if m.get("drastic")][:6]
    seen = set()
    deduped = []
    for m in radical:
        t = (m.get("ticker") or "").upper()
        if not t or t in seen:
            continue
        seen.add(t)
        deduped.append(m)
    radical = deduped

    for m in radical:
        sym = (m.get("ticker") or "").upper()
        m["held"] = m.get("held", sym in held)
        if sym in held:
            leg = holdings.get(sym, {})
            qty = float(leg.get("quantity") or 0)
            mv = float(leg.get("market_value") or 0)
            m["exposure"] = {
                "quantity": qty,
                "market_value": mv,
                "asset_type": leg.get("asset_type"),
                "unrealized_pnl": leg.get("unrealized_profit_loss"),
                "contract": leg.get("description") or (leg.get("symbol") if leg.get("asset_type") == "OPTION" else None),
            }
        m["on_board"] = sym in on_board
        m["cause"] = _mover_cause(sym)

        exposure = m.get("exposure") or {}
        mv = exposure.get("market_value")
        if m["held"] and mv:
            pct = float(m.get("chg_pct") or 0) / 100.0
            m["est_book_impact"] = round(float(mv) * pct, 2)
        else:
            m["est_book_impact"] = 0.0

        if m["held"]:
            m["stance"] = (
                "IN YOUR BOOK — check the stop and whether the move validates or invalidates the thesis "
                f"before you add. Estimated book impact ${abs(m.get('est_book_impact') or 0):,.0f}."
            )
        elif m.get("on_board"):
            m["stance"] = "ON TODAY'S BOARD — the setup's entry zone just moved; re-check distance and R:R before acting."
        else:
            m["stance"] = "Not held and not on the board — informational only. Do not chase."

    return radical


def _mover_cause(sym: str) -> str:
    """Best-effort reason for a move: earnings date, or a headline."""
    try:
        from src.clients.news_client import get_ticker_news
        res = get_ticker_news(sym, days=2) or {}
        head = res.get("catalyst") or res.get("headline") or ""
        if head and "No recent news" not in str(head):
            return str(head)[:150]
    except Exception as e:
        logger.debug(f"Mover cause news failed for {sym}: {e}")
    try:
        days = _days_to_earnings(sym)
        if days is not None and days <= 2:
            return f"Earnings within {days} day(s) — event-driven move."
    except Exception:
        pass
    return "No single catalyst captured — treat as flow/liquidity driven."


# ─────────────────────────────────────────────────────────────────────────────
# 4. CALIBRATION PANEL — what has actually worked for this user
# ─────────────────────────────────────────────────────────────────────────────

def _scorecard_of(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate R outcomes for one homogeneous pool."""
    wins = losses = scratches = 0
    total_r = 0.0
    win_r_sum = loss_r_sum = 0.0
    for r in rows:
        v = r.get("realized_r")
        if v is None:
            continue
        v = float(v)
        if abs(v) < 0.1:
            scratches += 1
        elif v >= 0.1:
            wins += 1
            win_r_sum += v
            total_r += v
        else:
            losses += 1
            loss_r_sum += abs(v)
            total_r += v
    scored = wins + losses + scratches
    win_rate = round((wins / scored) * 100.0, 2) if scored else None
    avg_win = round(win_r_sum / wins, 2) if wins else None
    avg_loss = round(loss_r_sum / losses, 2) if losses else None
    profit_factor = round((win_r_sum / loss_r_sum), 2) if loss_r_sum > 0 else None
    expectancy = round(total_r / scored, 3) if scored else None
    # Breakeven win rate implied by THIS payoff ratio, not an assumed 2:1.
    breakeven = None
    if avg_win and avg_loss:
        breakeven = round(100.0 / (1.0 + (avg_win / avg_loss)), 2)
    return {
        "closed": len(rows),
        "scored": scored,
        "unmeasured": len(rows) - scored,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "win_rate_pct": win_rate,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "breakeven_win_rate_pct": breakeven,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "total_realized_r": round(total_r, 2),
    }


def _real_trade_coverage() -> Dict[str, Any]:
    """How much of the real closed-trade history is actually measurable."""
    out: Dict[str, Any] = {"available": False}
    try:
        import sqlite3
        from src.ui.services.feedback_service import DB_PATH
        if not DB_PATH.exists():
            return out
        conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM positions WHERE status='CLOSED'")
        total = int(cur.fetchone()["n"])
        cur.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status='CLOSED' AND exit_price IS NOT NULL AND exit_price > 0"
        )
        with_exit = int(cur.fetchone()["n"])
        cur.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status='CLOSED' AND (initial_stop IS NOT NULL OR stop IS NOT NULL)"
        )
        with_stop = int(cur.fetchone()["n"])
        cur.execute(
            "SELECT MIN(closed_at) AS first, MAX(closed_at) AS last FROM positions WHERE status='CLOSED'"
        )
        span = cur.fetchone()
        bad = cur.execute(
            """
            SELECT substr(closed_at,1,7) AS mon,
                   SUM(CASE WHEN exit_price IS NULL OR exit_price <= 0 THEN 1 ELSE 0 END) AS missing_exit,
                   COUNT(*) AS n
            FROM positions WHERE status='CLOSED' GROUP BY mon ORDER BY mon DESC LIMIT 3
            """
        ).fetchall()
        out.update({
            "available": True,
            "closed_total": total,
            "with_exit_price": with_exit,
            "with_stop": with_stop,
            "exit_coverage_pct": round((with_exit / total) * 100.0, 1) if total else None,
            "stop_coverage_pct": round((with_stop / total) * 100.0, 1) if total else None,
            "span": {"first": span["first"], "last": span["last"]},
            "worst_months": [dict(r) for r in bad],
        })
    except Exception as e:
        logger.debug(f"Coverage audit failed: {e}")
    return out


def get_calibration_panel() -> Dict[str, Any]:
    """Separate what the user ACTUALLY did from what the model says, and say how much is missing."""
    panel: Dict[str, Any] = {"reasoning": []}
    try:
        from src.ui.services.feedback_service import get_feedback_loop_data
        fb = get_feedback_loop_data() or {}
        closed = fb.get("closed_positions") or []

        real_rows = [c for c in closed if str(c.get("pool", "real")).lower() != "model"]
        model_rows = [c for c in closed if str(c.get("pool", "")).lower() == "model"]

        real_sc = _scorecard_of(real_rows)
        model_sc = _scorecard_of(model_rows)

        panel["real"] = real_sc
        panel["model"] = model_sc
        try:
            from src.tracking.thesis_evaluator import get_paper_trade_stats
            paper = get_paper_trade_stats()
        except Exception as e:
            logger.debug(f"Paper trade stats failed: {e}")
            paper = {}
        if paper.get("available"):
            panel["paper"] = paper
        else:
            panel.pop("paper", None)
        panel["pool_split"] = {
            "real_rows": len(real_rows),
            "model_rows": len(model_rows),
            "paper_rows": (paper.get("open") or 0) + ((paper.get("aggregate") or {}).get("resolved") or 0),
            "note": (
                "REAL = trades the tracker actually opened/closed. MODEL = the bar-walk lifecycle "
                "simulator's backtest on suggested setups. PAPER = a deterministic 100-share position "
                "opened at the spot captured when the thesis was generated, then carried forward on "
                "live quotes until the thesis resolves. These are different populations and are never "
                "blended into one headline number."
            ),
        }
        panel["coverage"] = _real_trade_coverage()
        panel["open_risk"] = {
            "count": len(fb.get("open_positions") or []),
            "symbols": [p.get("ticker") for p in (fb.get("open_positions") or [])][:10],
        }

        # Reasoning is driven by the REAL pool when it has a usable sample, and says so otherwise.
        reason_pool = real_sc if (real_sc.get("scored") or 0) >= 10 else model_sc
        pool_label = "your executed trades" if reason_pool is real_sc else "the model's simulated outcomes (insufficient real-trade sample)"
        reasoning: List[str] = []

        wr = reason_pool.get("win_rate_pct")
        be = reason_pool.get("breakeven_win_rate_pct")
        pf = reason_pool.get("profit_factor")
        exp_r = reason_pool.get("expectancy")
        scored = reason_pool.get("scored") or 0

        if wr is not None and be is not None:
            if wr >= be + 5:
                reasoning.append(
                    f"On {pool_label}, a {wr:.0f}% win rate beats the {be:.0f}% breakeven implied by your own payoff ratio ({reason_pool.get('avg_win_r')}R win / {reason_pool.get('avg_loss_r')}R loss) — the edge is in the payoff, not the hit rate, so protect the exit discipline that produces it."
                )
            elif wr >= be - 5:
                reasoning.append(
                    f"A {wr:.0f}% win rate sits right on the {be:.0f}% breakeven for your payoff ratio — this is a coin-flip dressed as a system; tighten entry timing before adding size."
                )
            else:
                reasoning.append(
                    f"A {wr:.0f}% win rate is below the {be:.0f}% breakeven implied by your payoff ratio — the filter is admitting too many losers, so the fix is entry quality, not bigger size."
                )
        if pf is not None:
            if pf >= 1.5:
                reasoning.append(f"Profit factor {pf:.2f} on {scored} scored trades — winners more than cover losers. Do not touch the rules.")
            elif pf >= 1.2:
                reasoning.append(f"Profit factor {pf:.2f} — positive but thin. Cut the weakest lane rather than adding new ones.")
            else:
                reasoning.append(f"Profit factor {pf:.2f} — the aggregate edge is not yet proven; treat every new lane as unmeasured.")
        if exp_r is not None:
            reasoning.append(f"Expectancy {exp_r:+.2f}R per trade across {scored} scored trades.")

        # Thesis-attribution pool: the deterministic 100-share paper position taken at the
        # thesis spot. This is the only pool that links a generated thesis to its outcome, so
        # it is the one that can say whether the screen's calls were actually right.
        paper = panel.get("paper") or {}
        agg_p = paper.get("aggregate") or {}
        if (agg_p.get("resolved") or 0) >= 5:
            correct = agg_p.get("thesis_correct_rate_pct")
            avg_r = agg_p.get("avg_r")
            pf_p = agg_p.get("profit_factor")
            reasoning.append(
                f"THESIS ATTRIBUTION ({agg_p.get('resolved')} resolved paper trades at 100 shares from the thesis spot): "
                f"{correct}% of theses reached their target, {agg_p.get('stop_hit')} hit their stop first, "
                f"average {avg_r}R with a {pf_p} profit factor — this is the screen's own hit rate, "
                f"independent of how you executed."
            )
        elif (paper.get("open") or 0) > 0 and not (agg_p.get("resolved") or 0):
            reasoning.append(
                f"THESIS ATTRIBUTION: {paper.get('open')} paper position(s) are still carrying forward on live quotes — "
                f"no thesis has resolved yet, so the screen's hit rate is not yet measurable."
            )

        cov = panel.get("coverage") or {}
        if cov.get("available") and cov.get("closed_total"):
            if (cov.get("exit_coverage_pct") or 0) < 90:
                worst = cov.get("worst_months") or []
                w = worst[0] if worst else {}
                reasoning.append(
                    f"DATA CAVEAT — only {cov.get('with_exit_price')}/{cov.get('closed_total')} closed trades ({cov.get('exit_coverage_pct')}%) recorded an exit price and {cov.get('stop_coverage_pct')}% a stop level"
                    + (f"; {w.get('mon')} is missing exits on {w.get('missing_exit')} of {w.get('n')} trades" if w else "")
                    + ". Trades without a stop cannot produce an R multiple, so they are counted as unmeasured rather than assumed breakeven — the real win rate therefore rests on a smaller sample than the raw trade count implies."
                )
        if not reasoning:
            reasoning.append("Not enough closed trades to calibrate on yet — this panel becomes meaningful after ~20 scored real exits.")
        panel["reasoning"] = reasoning
    except Exception as e:
        logger.debug(f"Calibration panel failed: {e}")

    try:
        from src.tracking.forward_log import get_forward_summary
        fwd = get_forward_summary() or {}
        rules = fwd.get("rules") or {}
        pending = []
        for name, stats in (rules.items() if isinstance(rules, dict) else []):
            n = (stats or {}).get("observations", 0)
            need = (stats or {}).get("min_required", fwd.get("min_observations", 200))
            try:
                n = int(n)
            except Exception:
                n = 0
            if n < int(need or 0):
                pending.append({"rule": name, "observations": n, "required": int(need or 0)})
        if pending:
            panel["unmeasured_lanes"] = pending[:6]
    except Exception as e:
        logger.debug(f"Forward log calibration failed: {e}")

    return panel


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _holdings_by_underlying() -> Dict[str, Dict[str, Any]]:
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions
    out: Dict[str, Dict[str, Any]] = {}
    for p in get_portfolio_positions() or []:
        s = (p.get("underlying_symbol") or p.get("symbol") or "").upper().strip()
        if not s:
            continue
        agg = out.setdefault(s, {
            "quantity": 0.0, "market_value": 0.0, "unrealized_profit_loss": 0.0,
            "asset_type": p.get("asset_type"), "symbol": p.get("symbol"),
            "description": p.get("description"), "option_strike": p.get("option_strike"),
            "option_expiration": p.get("option_expiration"), "option_type": p.get("option_type"),
        })
        agg["quantity"] += float(p.get("quantity") or 0.0)
        agg["market_value"] += float(p.get("market_value") or 0.0)
        agg["unrealized_profit_loss"] += float(p.get("unrealized_profit_loss") or 0.0)
    return out


_EARN_CACHE: Dict[str, Optional[int]] = {}

_PAPER_REFRESH_T: float = 0.0
_PAPER_REFRESH_EVERY: float = 300.0


def _maybe_refresh_paper() -> None:
    """Throttled paper-thesis resolution so the desk shows fresh outcomes without blocking."""
    global _PAPER_REFRESH_T
    now_ts = time.time()
    if (now_ts - _PAPER_REFRESH_T) < _PAPER_REFRESH_EVERY:
        return
    _PAPER_REFRESH_T = now_ts
    try:
        from src.tracking.thesis_evaluator import refresh_thesis_paper_trades
        refresh_thesis_paper_trades()
    except Exception as e:
        logger.debug(f"Throttled paper refresh failed: {e}")


def _days_to_earnings(sym: str) -> Optional[int]:
    """Cached days-to-next-earnings lookup. Never raises; None means 'unknown'."""
    key = (sym or "").upper().strip()
    if not key:
        return None
    if key in _EARN_CACHE:
        return _EARN_CACHE[key]
    try:
        from src.clients.earnings_client import get_next_earnings_days
        days = get_next_earnings_days(key)
        days = int(days) if days is not None else None
    except Exception as e:
        logger.debug(f"Earnings lookup failed for {key}: {e}")
        days = None
    if len(_EARN_CACHE) > 400:
        _EARN_CACHE.clear()
    _EARN_CACHE[key] = days
    return days


def build_action_panel(opportunities: Optional[List[Dict[str, Any]]] = None,
                       regime: Optional[Dict[str, Any]] = None,
                       open_positions: Optional[List[Dict[str, Any]]] = None,
                       force: bool = False) -> Dict[str, Any]:
    """Everything the desk needs in one cached payload."""
    global _CACHE, _CACHE_T
    now_ts = time.time()
    if not force and _CACHE and (now_ts - _CACHE_T) < _TTL:
        return _CACHE

    opportunities = opportunities or []
    regime = regime or {}
    open_positions = open_positions or []
    movers_raw = regime.get("movers") or {}
    movers = list(movers_raw.get("alerts") or [])
    movers += list(movers_raw.get("spikes") or [])
    movers += list(movers_raw.get("drops") or [])

    # Resolve paper theses on a slower cadence than the panel cache: the bar walk is the
    # expensive part (~1s per 5 positions) and the answer only changes once a day.
    _maybe_refresh_paper()

    def _safe(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            logger.debug(f"action panel {fn.__name__} failed: {e}")
            return {}

    bat = _safe(get_bat_signal, opportunities, regime, open_positions)
    flags = _safe(get_risk_flags, opportunities)
    radar = _safe(get_event_radar, movers, opportunities)
    calib = _safe(get_calibration_panel)

    panel = {
        "available": bool(bat),
        "bat_signal": bat,
        "risk_flags": flags,
        "event_radar": radar,
        "calibration": calib,
        "as_of_et": datetime.now(ZoneInfo("America/New_York")).strftime("%I:%M %p ET"),
    }
    _CACHE, _CACHE_T = panel, time.time()
    return panel
