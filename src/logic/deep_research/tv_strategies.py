"""tv_strategies.py — load and geometry-validate TradingView Strategy Finder results."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def load_tv_strategies(ticker: str, tdir: Path, raw_dir: Path, dw_dict: dict) -> str:
    """Load ``{ticker}_tv_strategies.json``, validate strike geometry, and return
    a formatted markdown table string (or empty string when nothing valid is found).
    """
    safe = ticker.replace(":", "_")
    tv_strat_path = tdir / f"{safe}_tv_strategies.json"
    if not tv_strat_path.exists():
        candidate = raw_dir / f"{safe}_tv_strategies.json"
        if candidate.exists():
            tv_strat_path = candidate

    if not tv_strat_path.exists():
        return ""

    try:
        strat_data = json.loads(tv_strat_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[{ticker}] Error loading tv_strategies: {e}")
        return ""

    if not strat_data:
        return ""

    from src.logic.strike_validator import validate_strike_geometry

    dw_spot = float(dw_dict.get("close") or 0.0) if isinstance(dw_dict, dict) else 0.0
    dw_exp_move = (
        float(dw_dict.get("Exp Move Pct 21b") or dw_dict.get("exp_move_pct") or 0.0)
        if isinstance(dw_dict, dict)
        else None
    )

    valid_strats = []
    rejected_count = 0

    for s in strat_data:
        formula = s.get("formula", "")
        stype = (s.get("strategy_type", "") or "").upper().replace(" ", "_")
        strikes = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*[CPcp]\b", formula)]

        extra_short_st = None
        if "JADE_LIZARD" in stype or "JADE" in stype:
            puts = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*[Pp]\b", formula)]
            calls = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*[Cc]\b", formula)]
            if len(puts) >= 1 and len(calls) >= 2:
                extra_short_st = puts[0]
                short_st = min(calls)
                long_st = max(calls)
            else:
                long_st, short_st = None, None
        elif len(strikes) < 2:
            if any(tag in stype for tag in ("SHORT", "COVERED", "SELL", "CASH_SECURED", "CSP", "WRITE")):
                short_st = strikes[0] if strikes else None
                long_st = None
            else:
                long_st = strikes[0] if strikes else None
                short_st = None
        else:
            lo, hi = min(strikes), max(strikes)
            is_put = bool(re.search(r"\d+\s*[Pp]\b", formula)) or "PUT" in stype
            is_credit = (
                any(tag in stype for tag in ("BEAR_CALL", "CALL_CREDIT", "BULL_PUT", "PUT_CREDIT"))
                or "CREDIT" in stype
            )
            if is_credit:
                short_st, long_st = (hi, lo) if is_put else (lo, hi)
            else:
                long_st, short_st = (hi, lo) if is_put else (lo, hi)

        try:
            max_profit_val = float(s.get("max_profit", 0) or 0) if s.get("max_profit") is not None else None
            max_loss_val = float(s.get("max_loss", 0) or 0) if s.get("max_loss") is not None else None
        except (ValueError, TypeError):
            max_profit_val = None
            max_loss_val = None

        is_valid, defects = validate_strike_geometry(
            strategy_type=s.get("strategy_type", ""),
            spot_price=dw_spot,
            exp_move_pct_21b=dw_exp_move,
            long_strike=long_st,
            short_strike=short_st,
            extra_short_strike=extra_short_st,
            max_profit=max_profit_val,
            max_loss=max_loss_val,
        )
        if is_valid:
            valid_strats.append(s)
        else:
            rejected_count += 1
            logger.info(f"[{ticker}] Strategy Filter rejected '{formula}': {', '.join(defects)}")

    if valid_strats:
        rows = [
            f"--- TRADINGVIEW STRATEGY FINDER (GEOMETRY-VALIDATED STRUCTURES FOR {ticker}) ---",
            "(Note: Geometry-validated implies strikes are OTM / within ExpMove bounds. "
            "Naked short puts & Jade Lizards carry undefined downside assignment risk on 100 shares.)",
            "| Expiry | Days | Strategy | Formula/Strikes | Max Profit | Max Loss / Risk Profile | R:R | Breakeven |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in valid_strats[:12]:
            raw_loss = s.get("max_loss")
            try:
                loss_val = float(raw_loss or 0)
            except (ValueError, TypeError):
                loss_val = 0.0

            stype_name = s.get("strategy_type", "")
            is_undefined_risk = (
                abs(loss_val) > (dw_spot * 100 * 0.5)
                or stype_name in ("Short Put", "Jade Lizard", "Cash Secured Put")
                or loss_val < -50000
            )
            loss_str = "Undefined (Assignment Risk on 100sh)" if is_undefined_risk else str(raw_loss)

            rows.append(
                f"| {s.get('expiration')} | {s.get('days')} | {stype_name} | "
                f"{s.get('formula')} | {s.get('max_profit')} | {loss_str} | "
                f"{s.get('reward_risk')} | {s.get('breakeven')} |"
            )
        return "\n".join(rows)

    if rejected_count > 0:
        return (
            f"--- TRADINGVIEW STRATEGY FINDER ({ticker}) ---\n"
            f"The Strategy Finder returned {len(strat_data)} pre-computed spreads, "
            f"but ALL {rejected_count} were rejected by the deterministic strike geometry validator "
            f"(ITM short legs, naked-long disguises, or accounting mismatches). "
            f"Do NOT invent a spread. Use the live `fetch_options_chain` and `scrape_tradingview_options_finder` tools "
            f"to find a valid structure, or recommend SKIP if no clean geometry exists."
        )

    return ""
