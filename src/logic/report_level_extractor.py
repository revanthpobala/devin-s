"""
Report Level Extractor
Parses generated research reports (summary + arbitration) to extract structured
tactical levels, options plays, and invalidation triggers.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from src import config

logger = logging.getLogger(__name__)


def _safe_float(val: Any, default: Optional[float] = 0.0) -> Optional[float]:
    if val is None:
        return default
    try:
        s = str(val).replace(",", "").strip().rstrip(".")
        if s.startswith("$"):
            s = s[1:].strip().rstrip(".")
        return float(s)
    except (ValueError, TypeError):
        return default


def _credit_strike_em_consistent(structure: str, short_strike: float, spot_price: float, exp_move_pct: float) -> bool:
    """Return True if a premium-sale (credit) structure's short strike is scaled to the
    Expected Move at >= 1.25x. Encodes revanth-original-gem.md:341 — high IV Rank alone
    does NOT justify selling premium; the strike must sit beyond 1.25x the expected move.

    - BULL_PUT_SPREAD / CASH_SECURED_PUT: short strike must be <= spot * (1 - 1.25*EM/100).
    - BEAR_CALL_SPREAD: short strike must be >= spot * (1 + 1.25*EM/100).
    - DEBIT structures and LONG single-legs are never gated here (they are the buyer's side).
    - If EM or spot is unavailable, default to consistent (True) so we don't force a demotion
      on missing data — the LLM-side gem rules still apply.

    `exp_move_pct` is a percentage already (e.g. 9.90 means a 9.9% expected move).
    """
    if structure not in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "CASH_SECURED_PUT"):
        return True
    try:
        em = abs(float(exp_move_pct))
        spot = float(spot_price)
        k = float(short_strike)
    except (TypeError, ValueError):
        return True
    if not spot or not em or not k:
        return True
    if structure == "BEAR_CALL_SPREAD":
        return k >= spot * (1.0 + 1.25 * em / 100.0)
    # Bull put / CSP: short strike must be priced below the 1.25x EM floor
    return k <= spot * (1.0 - 1.25 * em / 100.0)


def _dw_lookup(dw_data: Dict[str, Any], *keys: str) -> float:
    """Case-insensitive data-window field lookup returning a float (0.0 if absent/invalid).

    The data window JSON uses CSV headers like "Close" and "Exp Move % (21b)"; callers may
    pass several candidate keys and we match the first that is present and numeric.
    """
    lower_map = {str(k).lower(): v for k, v in (dw_data or {}).items()}
    for key in keys:
        val = dw_data.get(key) if dw_data else None
        if val is None:
            val = lower_map.get(str(key).lower())
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0


def _validate_and_demote_credit_spread(
    options_struct: str,
    short_strike: float,
    long_strike: float,
    target_credit: float,
    spot_price: float,
    tactical_stop: float,
    em_pct: float,
    opt_summary: str,
    safe_ticker: str,
) -> tuple:
    """Return (options_struct, opt_summary, clear_strikes) after enforcing strict risk/reward
    and technical soundness on credit spreads (BULL_PUT_SPREAD, BEAR_CALL_SPREAD, CASH_SECURED_PUT).

    Fatal flaws that force demotion to NON-ACTIONABLE:
    1. Short strike sits at or inside the tactical stop loss (stop-out triggers ITM options loss).
    2. Short strike is too close to spot (< 4.0% below spot or inside 1.25x Expected Move).
    3. Credit-to-width ratio is below 22% (risking > 3.5:1 against the trader).
    """
    struct_upper = (options_struct or "").upper()
    if struct_upper not in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "CASH_SECURED_PUT"):
        return options_struct, opt_summary, False

    reasons = []
    # 1. Stop loss containment check
    if struct_upper in ("BULL_PUT_SPREAD", "CASH_SECURED_PUT") and tactical_stop > 0 and short_strike > 0:
        if short_strike >= tactical_stop:
            reasons.append(f"short put (${short_strike:.2f}) sits at/above tactical stop (${tactical_stop:.2f})")
    elif struct_upper == "BEAR_CALL_SPREAD" and tactical_stop > 0 and short_strike > 0:
        if short_strike <= tactical_stop:
            reasons.append(f"short call (${short_strike:.2f}) sits at/below tactical stop (${tactical_stop:.2f})")

    # 2. Distance to spot / Expected Move check
    if spot_price > 0 and short_strike > 0:
        dist_pct = abs(spot_price - short_strike) / spot_price
        if dist_pct < 0.04:
            reasons.append(f"short strike (${short_strike:.2f}) is only {dist_pct*100:.1f}% from spot (${spot_price:.2f})")
        elif em_pct > 0 and not _credit_strike_em_consistent(struct_upper, short_strike, spot_price, em_pct):
            reasons.append(f"short strike (${short_strike:.2f}) is inside 1.25x ExpMove ({em_pct:.1f}%)")

    # 3. Credit-to-width ratio (must collect >= 22% of spread width)
    if short_strike > 0 and long_strike > 0:
        width = abs(short_strike - long_strike)
        if width > 0 and target_credit > 0:
            cr_ratio = target_credit / width
            if cr_ratio < 0.22:
                reasons.append(f"credit ${target_credit:.2f} is only {cr_ratio*100:.0f}% of ${width:.2f} width (risking {1/cr_ratio-1:.1f}:1)")

    if reasons:
        reason_str = "; ".join(reasons)
        logger.info(f"[{safe_ticker}] Demoting credit spread {struct_upper}: {reason_str}")
        return (
            "NONE",
            f"{opt_summary} [DEMOTED: {reason_str} — negative expectancy; use equity or Bull Call debit spread]",
            True,
        )

    return options_struct, opt_summary, False


def _demote_unscaled_credit(
    options_struct: str,
    short_strike: float,
    spot_price: float,
    em_pct: float,
    opt_summary: str,
    safe_ticker: str,
    long_strike: float = 0.0,
    target_credit: float = 0.0,
    tactical_stop: float = 0.0,
) -> tuple:
    return _validate_and_demote_credit_spread(
        options_struct=options_struct,
        short_strike=short_strike,
        long_strike=long_strike,
        target_credit=target_credit,
        spot_price=spot_price,
        tactical_stop=tactical_stop,
        em_pct=em_pct,
        opt_summary=opt_summary,
        safe_ticker=safe_ticker,
    )


def _apply_options_cleanup(data: dict, dw_data: dict, spot_price: float, safe_ticker: str) -> None:
    """Helper to clean up defective options plans across all report parsing paths."""
    options_p = data.get("options_plan")
    if not options_p:
        return
    shares_p = data.get("shares_plan") or {}
    tactical_stop = float(shares_p.get("tactical_stop") or data.get("tactical_stop") or 0.0)
    em_pct = _dw_lookup(dw_data, "Exp Move % (21b)", "exp_move_pct", "Exp Move Pct 21b")
    blk_struct = options_p.get("structure") or "NONE"
    blk_short = float(options_p.get("short_strike") or 0.0)
    blk_long = float(options_p.get("long_strike") or 0.0)
    blk_credit = float(options_p.get("target_credit") or 0.0)
    blk_spot = float(data.get("spot_price") or spot_price or 0.0)

    new_struct, new_summary, clear_strikes = _validate_and_demote_credit_spread(
        blk_struct, blk_short, blk_long, blk_credit, blk_spot, tactical_stop, em_pct, options_p.get("summary", ""), safe_ticker
    )
    if clear_strikes:
        options_p["structure"] = "NONE"
        options_p["long_strike"] = 0.0
        options_p["short_strike"] = 0.0
        options_p["actionable"] = False
        options_p["entry_trigger"] = "NONE"
        options_p["summary"] = new_summary
        options_p["target_debit"] = 0.0
        options_p["target_credit"] = 0.0
        options_p["max_loss"] = 0.0
        options_p["max_profit"] = 0.0
        options_menu = data.get("options_menu") or {}
        if "tactical_spread" in options_menu:
            options_menu["tactical_spread"]["structure"] = "NONE"
            options_menu["tactical_spread"]["long_strike"] = 0.0
            options_menu["tactical_spread"]["short_strike"] = 0.0
            options_menu["tactical_spread"]["target_debit"] = 0.0
            options_menu["tactical_spread"]["target_credit"] = 0.0


def extract_watch_levels_from_report(ticker: str, date_str: str) -> Optional[Dict[str, Any]]:
    """Extract structured watch levels for a ticker on a date.

    Priority:
    1. Direct embedded ```json:watch_levels code block in <ticker>_arbitration.md or <ticker>_summary.md
    2. Deterministic regex extraction across the Arbitration Directive and Quantitative Plan
    3. Fallback to Data Window JSON if available
    """
    safe_ticker = ticker.replace(":", "_").upper()
    reports_dir = config.BASE_DIR / "reports" / date_str
    raw_dir = config.BASE_DIR / "data" / "raw" / date_str / safe_ticker
    triage_dir = config.BASE_DIR / "data" / "triage" / date_str

    summary_file = reports_dir / f"{safe_ticker}_summary.md"
    arbitration_file = reports_dir / f"{safe_ticker}_arbitration.md"
    dw_file = raw_dir / f"{safe_ticker}_datawindow.json"

    # Search triage directories if not in raw
    if not dw_file.exists():
        for sub in ("_DEEP_RESEARCH", "force"):
            cand = triage_dir / sub / safe_ticker / f"{safe_ticker}_datawindow.json"
            if cand.exists():
                dw_file = cand
                break

    summary_text = summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""
    arbitration_text = arbitration_file.read_text(encoding="utf-8") if arbitration_file.exists() else ""
    dw_data = json.loads(dw_file.read_text(encoding="utf-8")) if dw_file.exists() else {}

    if not summary_text and not arbitration_text and not dw_data:
        logger.warning(f"[{safe_ticker}] No reports or datawindow found for date {date_str}.")
        return None

    # Spot Price — resolved up front so all downstream parsing & validation can use it.
    spot_price = _dw_lookup(dw_data, "close", "Close")
    if not spot_price:
        m_spot = re.search(r"Bar close:\s*\$([0-9,.]+)", summary_text)
        if m_spot:
            spot_price = _safe_float(m_spot.group(1)) or 0.0
        elif arbitration_text:
            m_spot_arb = re.search(r"(?:Spot Price|Price|spot|spot at):\s*\$([0-9,.]+)", arbitration_text, re.IGNORECASE)
            if m_spot_arb:
                spot_price = _safe_float(m_spot_arb.group(1)) or 0.0

    # Before extraction, if _arbitration.md contains NO_LEVELS or LEVEL GATE REJECTED,
    # try to parse the embedded JSON block anyway so the briefing/UI can show the thesis card.
    # Return it with level_gate_rejected=True; callers must not treat it as fully actionable.
    if arbitration_text and ("NO_LEVELS" in arbitration_text or "LEVEL GATE REJECTED" in arbitration_text):
        logger.info(f"[{safe_ticker}] Arbitration gate-rejected — attempting display-only parse.")
        m_block = re.search(
            r"```(?:json)?(?::watch_levels|\s+watch_levels)?\s*(\{[\s\S]*?\"shares_plan\"[\s\S]*?\})\s*```",
            arbitration_text,
        )
        if m_block:
            try:
                data = json.loads(m_block.group(1))
                data.setdefault("ticker", safe_ticker)
                data.setdefault("date", date_str)
                data["level_gate_rejected"] = True
                _apply_options_cleanup(data, dw_data, spot_price, safe_ticker)
                if "options_plan" in data and isinstance(data["options_plan"], dict):
                    data["options_plan"]["actionable"] = False
                logger.info(f"[{safe_ticker}] Returning gate-rejected watch_levels for display (verdict={data.get('verdict')}).")
                return data
            except Exception as e:
                logger.debug(f"[{safe_ticker}] Could not parse gate-rejected block: {e}")
        logger.info(f"[{safe_ticker}] Arbitration contains NO_LEVELS or LEVEL GATE REJECTED. Skipping extraction.")
        return None

    # ── 1. Embedded JSON Block Check ───────────────────────────────────────
    for text_source in (arbitration_text, summary_text):
        if not text_source:
            continue
        m_block = re.search(
            r"```(?:json)?(?::watch_levels|\s+watch_levels)?\s*(\{[\s\S]*?\"shares_plan\"[\s\S]*?\})\s*```",
            text_source,
        )
        if m_block:
            try:
                data = json.loads(m_block.group(1))
                data.setdefault("ticker", safe_ticker)
                data.setdefault("date", date_str)
                shares_p = data.setdefault("shares_plan", {})
                
                # Detect side
                side_val = data.get("side") or shares_p.get("side")
                if not side_val:
                    if re.search(r"\b(SHORT|BEARISH PUT|BEAR CALL)\b", text_source):
                        side_val = "SHORT"
                    else:
                        side_val = "LONG"
                data["side"] = side_val
                shares_p["side"] = side_val

                # Extract breakout levels from report text if missing from embedded json
                combined_txt = (arbitration_text + "\n" + summary_text)
                clean_txt = re.sub(r"[*_`]+", "", combined_txt)
                if "breakout_level" not in shares_p:
                    m_bo = re.search(
                        r"(?:Buy-Stop on daily close above|Buy-Stop above|Breakout Level:?|breakout above)\s*\$?([0-9,]+(?:\.[0-9]+)?)",
                        clean_txt,
                        re.IGNORECASE,
                    )
                    if not m_bo:
                        m_bo = re.search(
                            r"(?:Darvas Box Top[^\$]*\$)\s*\$?([0-9,]+(?:\.[0-9]+)?)",
                            clean_txt,
                            re.IGNORECASE,
                        )
                    if m_bo:
                        try:
                            shares_p["breakout_level"] = _safe_float(m_bo.group(1))
                        except Exception:
                            pass
                if "breakout_stop" not in shares_p:
                    m_bostop = re.search(
                        r"(?:stop back to|breakout stop:?)\s*\$?([0-9,]+(?:\.[0-9]+)?)",
                        clean_txt,
                        re.IGNORECASE,
                    )
                    if m_bostop:
                        try:
                            shares_p["breakout_stop"] = _safe_float(m_bostop.group(1))
                        except Exception:
                            pass

                if "proximity_buffer_pct" not in shares_p:
                    shares_p["proximity_buffer_pct"] = 1.0
                options_p = data.setdefault("options_plan", {})
                if "actionable" not in options_p:
                    options_p["actionable"] = bool(options_p.get("structure") not in ("NONE", "", None))
                if "entry_trigger" not in options_p:
                    options_p["entry_trigger"] = "AT_FLOOR_LIMIT" if options_p.get("actionable") else "NONE"
                if "target_credit" not in options_p:
                    options_p["target_credit"] = 0.0

                # Ensure options_menu exists and has tiered options
                options_menu = data.setdefault("options_menu", {})
                if "tactical_spread" not in options_menu:
                    options_menu["tactical_spread"] = dict(options_p)
                combined_txt = arbitration_text + "\n" + summary_text
                clean_txt = re.sub(r"[*_`]+", "", combined_txt)
                if "leaps" not in options_menu:
                    m_leaps = re.search(
                        r"(?:LEAPS|Long Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_txt, re.IGNORECASE
                    )
                    if m_leaps:
                        options_menu["leaps"] = {
                            "structure": "LONG_CALL",
                            "long_strike": _safe_float(m_leaps.group(1)),
                            "summary": f"${m_leaps.group(1)} Deep ITM LEAPS Call",
                        }
                    else:
                        options_menu["leaps"] = {
                            "structure": "NONE",
                            "summary": "No specific LEAPS strike designated.",
                        }
                if "income_or_csp" not in options_menu:
                    m_csp = re.search(
                        r"(?:Cash[- ]Secured Put|CSP|Short Put)[^\$]*\$?([0-9,.]+)\s*P?", clean_txt, re.IGNORECASE
                    )
                    m_cc = re.search(
                        r"(?:Covered Call|Sell Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_txt, re.IGNORECASE
                    )
                    if m_cc:
                        options_menu["income_or_csp"] = {
                            "structure": "COVERED_CALL",
                            "short_strike": _safe_float(m_cc.group(1)),
                            "summary": f"${m_cc.group(1)} Covered Call on long shares/LEAPS",
                        }
                    elif m_csp:
                        options_menu["income_or_csp"] = {
                            "structure": "CASH_SECURED_PUT",
                            "short_strike": _safe_float(m_csp.group(1)),
                            "summary": f"${m_csp.group(1)} Cash-Secured Put at support floor",
                        }
                    else:
                        options_menu["income_or_csp"] = {
                            "structure": "NONE",
                            "summary": "No income / CSP structure designated.",
                        }

                # Apply options plan cleanup and negative-expectancy credit spread demotion
                _apply_options_cleanup(data, dw_data, spot_price, safe_ticker)

                from src.logic.level_validation import validate_levels
                val_plan = {
                    **(data.get("shares_plan") or {}),
                    "options_plan": data.get("options_plan") or {},
                    "ticker": safe_ticker,
                    "date": date_str,
                    "side": data.get("side", "LONG"),
                    "setup_lane": data.get("setup_lane") or dw_data.get("setup_lane") or "WATCH_SHADOW",
                    "spot": spot_price,
                }
                v_ok, v_reasons = validate_levels(val_plan, dw_data, data.get("side", "LONG"), ticker=safe_ticker, date_str=date_str)
                data["level_gate_rejected"] = not v_ok
                data["gate_reasons"] = v_reasons

                # Persist to raw folder (never overwrite existing)
                save_path = raw_dir / f"{safe_ticker}_watch_levels.json"
                if not save_path.exists():
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    save_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                else:
                    logger.info(f"[{safe_ticker}] {save_path.name} already exists — preserving existing file without overwrite.")
                return data
            except Exception as e:
                logger.debug(f"[{safe_ticker}] Failed to parse embedded json block: {e}")

    # ── 2. Deterministic Regex Extraction Fallback ──────────────────────────
    combined_text = (arbitration_text + "\n" + summary_text)

    # Spot Price was resolved up front (above the embedded-block check).

    # Side
    side = "LONG"
    if re.search(
        r"(?:Verdict:\s*SHORT|###\s*Plan\s*[A-C]:\s*Short|Direction:\s*SHORT|Actionable Short|Bearish Play:\s*Put|Bear Put Spread)",
        combined_text,
        re.IGNORECASE,
    ):
        side = "SHORT"

    # Verdict & Conviction
    verdict = "NO_TRADE"
    m_verdict = re.search(r"\*\*Verdict:\*\*\s*([A-Za-z /()_-]+)", arbitration_text) or re.search(
        r"\*\*Verdict:\*\*\s*([A-Za-z /()_-]+)", summary_text
    )
    if m_verdict:
        v_raw = m_verdict.group(1).strip().upper()
        if any(neg in v_raw for neg in ("DO NOT", "DON'T", "NOT BUY", "NO BUY", "NO ENTRY", "NO_ENTRY", "NO TRADE", "NO_TRADE")):
            verdict = "NO_TRADE"
        elif re.search(r"\bSTALK\b", v_raw):
            verdict = "STALK"
        elif re.search(r"\bENTER\b", v_raw) or re.search(r"\bBUY\b", v_raw):
            verdict = "ENTER"
        elif re.search(r"\b(?:CASH|SKIP|AVOID)\b", v_raw):
            verdict = "CASH_SKIP"
        elif re.search(r"\bWATCH\b", v_raw):
            verdict = "WATCH"
        elif re.search(r"\bCUT\b", v_raw):
            verdict = "CUT"
        else:
            verdict = "NO_TRADE"

    conviction = 5
    m_conv = re.search(r"Conviction:\s*([0-9.]+)/10", arbitration_text) or re.search(
        r"Conviction:\s*([0-9.]+)/10", summary_text
    )
    if m_conv:
        try:
            conviction = int(float(m_conv.group(1)))
        except Exception:
            conviction = 5

    # Shares Plan (Entry Zone, Tactical Stop, Targets)
    entry_low = 0.0
    entry_high = 0.0
    m_zone = re.search(
        r"TACTICAL ENTRY ZONE:\s*\$([0-9,.]+)\s*–\s*\$([0-9,.]+)", summary_text, re.IGNORECASE
    ) or re.search(r"Entry Zone:\s*\$([0-9,.]+)\s*–\s*\$([0-9,.]+)", summary_text, re.IGNORECASE)
    if m_zone:
        entry_low = _safe_float(m_zone.group(1))
        entry_high = _safe_float(m_zone.group(2))
    elif "Long Entry Zone Bot" in dw_data and "Long Entry Zone Top" in dw_data:
        entry_low = _safe_float(dw_data.get("Long Entry Zone Bot"))
        entry_high = _safe_float(dw_data.get("Long Entry Zone Top"))

    tactical_stop = 0.0
    # Stop-to-breakeven must NOT be captured as initial tactical stop
    m_stop = None
    for m in re.finditer(r"(?:TACTICAL\s+STOP|Stop\s+Loss|Hard\s+Stop|Initial\s+Stop):\s*\$?([0-9,.]+)", arbitration_text, re.IGNORECASE):
        m_stop = m
        break
    if not m_stop:
        for m in re.finditer(r"stop(?:\s+loss)?\s+(?:at|to)\s*\*\*?\$([0-9,.]+)\*\*?", arbitration_text, re.IGNORECASE):
            start = max(0, m.start() - 40)
            end = min(len(arbitration_text), m.end() + 40)
            window = arbitration_text[start:end].lower()
            if "breakeven" in window or "break-even" in window or "trail" in window or "ratchet" in window:
                continue
            m_stop = m
            break
    if not m_stop:
        m_stop = (
            re.search(r"TACTICAL STOP:\s*\$([0-9,.]+)", summary_text, re.IGNORECASE)
            or re.search(r"Long Stop Loss:\s*\$([0-9,.]+)", summary_text, re.IGNORECASE)
        )
    if m_stop:
        tactical_stop = _safe_float(m_stop.group(1))
    elif "Long Stop Loss" in dw_data:
        tactical_stop = _safe_float(dw_data.get("Long Stop Loss"))

    target_1 = 0.0
    target_2 = 0.0
    m_t1 = re.search(r"TARGET 1:\s*\$([0-9,.]+)", summary_text, re.IGNORECASE) or re.search(
        r"Target 1 \(Trim\)\s*\|\s*\$([0-9,.]+)", summary_text, re.IGNORECASE
    )
    if m_t1:
        target_1 = _safe_float(m_t1.group(1))
    elif "Long Target" in dw_data:
        target_1 = _safe_float(dw_data.get("Long Target"))

    m_t2 = re.search(r"TARGET 2:\s*\$([0-9,.]+)", summary_text, re.IGNORECASE) or re.search(
        r"Target 2 \(Runner\)\s*\|\s*\$([0-9,.]+)", summary_text, re.IGNORECASE
    )
    if m_t2:
        target_2 = _safe_float(m_t2.group(1))

    # Breakout levels
    breakout_level = None
    breakout_stop = None
    clean_combined = re.sub(r"[*_`]+", "", combined_text)
    m_bo = re.search(
        r"(?:Buy-Stop on daily close above|Buy-Stop above|Breakout Level:?|breakout above)\s*\$?([0-9,]+(?:\.[0-9]+)?)",
        clean_combined,
        re.IGNORECASE,
    )
    if not m_bo:
        m_bo = re.search(
            r"(?:Darvas Box Top[^\$]*\$)\s*\$?([0-9,]+(?:\.[0-9]+)?)",
            clean_combined,
            re.IGNORECASE,
        )
    if m_bo:
        try:
            breakout_level = _safe_float(m_bo.group(1))
        except Exception:
            breakout_level = None
    m_bostop = re.search(
        r"(?:stop back to|breakout stop:?)\s*\$?([0-9,]+(?:\.[0-9]+)?)",
        clean_combined,
        re.IGNORECASE,
    )
    if m_bostop:
        try:
            breakout_stop = _safe_float(m_bostop.group(1))
        except Exception:
            breakout_stop = None

    # Entry Type
    entry_type = "LIMIT"
    if "NO ENTRY AT MARKET" in arbitration_text or "NO ENTRY" in arbitration_text:
        entry_type = "LIMIT"
    elif verdict == "ENTER" and entry_low <= spot_price <= entry_high:
        entry_type = "MARKET"

    # Options Plan
    options_struct = "NONE"
    exp_date = ""
    long_strike = 0.0
    short_strike = 0.0
    target_debit = 0.0
    max_loss = 0.0
    max_profit = 0.0
    opt_summary = "None"

    # 1. Bull Call Spread regex (supports multi-line across slash or newline)
    m_opt = (
        re.search(
            r"([A-Za-z]+\s+\d+,\s+\d{4})?\s*\$?([0-9,.]+)C?\s*(?:Call)?\s*(?:/|\n)\s*\$?([0-9,.]+)C?\s*(?:Call)?\s*Bull Call Spread",
            clean_combined,
            re.IGNORECASE,
        )
        or re.search(
            r"Buy\s+([A-Za-z]+ \d+,\s+\d{4})\s+\$?([0-9,.]+)\s+Call\s*(?:/|\n)\s*Sell\s+[A-Za-z]+ \d+,\s+\d{4}\s+\$?([0-9,.]+)\s+Call",
            clean_combined,
            re.IGNORECASE,
        )
        or re.search(
            r"Buy\s+([A-Za-z]+\s+\d+,\s+\d{4})?\s*\$?([0-9,.]+)\s+Call[\s\S]{1,80}?Sell\s+(?:[A-Za-z]+\s+\d+,\s+\d{4})?\s*\$?([0-9,.]+)\s+Call",
            clean_combined,
            re.IGNORECASE,
        )
    )
    if m_opt and m_opt.group(1):
        raw_exp = m_opt.group(1)
        options_struct = "BULL_CALL_SPREAD"
        long_strike = _safe_float(m_opt.group(2))
        short_strike = _safe_float(m_opt.group(3))
        try:
            from datetime import datetime

            exp_dt = datetime.strptime(raw_exp.strip(), "%b %d, %Y")
            exp_date = exp_dt.strftime("%Y-%m-%d")
        except Exception:
            exp_date = None
        opt_summary = f"{raw_exp} ${long_strike:.0f}/${short_strike:.0f} Bull Call Spread"
    else:
        # 2. Bull Put Spread regex (credit, supports multi-line)
        m_bps = re.search(
            r"([A-Za-z]+\s+\d+,\s+\d{4})?\s*\$?([0-9,.]+)P?\s*(?:/|\n)\s*\$?([0-9,.]+)P?\s*Bull Put Spread",
            clean_combined,
            re.IGNORECASE,
        )
        if m_bps:
            options_struct = "BULL_PUT_SPREAD"
            short_strike = _safe_float(m_bps.group(2))
            long_strike = _safe_float(m_bps.group(3))
            opt_summary = f"${short_strike:.0f}P/${long_strike:.0f}P Bull Put Spread"
        else:
            # 3. Bear Call Spread regex (credit, supports multi-line)
            m_bcs = re.search(
                r"([A-Za-z]+\s+\d+,\s+\d{4})?\s*\$?([0-9,.]+)C?\s*(?:/|\n)\s*\$?([0-9,.]+)C?\s*Bear Call Spread",
                clean_combined,
                re.IGNORECASE,
            )
            if m_bcs:
                options_struct = "BEAR_CALL_SPREAD"
                short_strike = _safe_float(m_bcs.group(2))
                long_strike = _safe_float(m_bcs.group(3))
                opt_summary = f"${short_strike:.0f}C/${long_strike:.0f}C Bear Call Spread"
            else:
                # 4. Bear Put Spread regex (debit, supports multi-line)
                m_bds = re.search(
                    r"([A-Za-z]+\s+\d+,\s+\d{4})?\s*\$?([0-9,.]+)P?\s*(?:/|\n)\s*\$?([0-9,.]+)P?\s*Bear Put Spread",
                    clean_combined,
                    re.IGNORECASE,
                )
                if m_bds:
                    options_struct = "BEAR_PUT_SPREAD"
                    long_strike = _safe_float(m_bds.group(2))
                    short_strike = _safe_float(m_bds.group(3))
                    opt_summary = f"${long_strike:.0f}P/${short_strike:.0f}P Bear Put Spread"
                else:
                    # 5. Outright Long Call / LEAPS
                    m_lc = re.search(
                        r"(?:Long Call|LEAPS Call|Buy Call)[^\$]*\$?([0-9,.]+)\s*Call",
                        clean_combined,
                        re.IGNORECASE,
                    )
                    if m_lc:
                        options_struct = "LONG_CALL"
                        long_strike = _safe_float(m_lc.group(1))
                        opt_summary = f"${long_strike:.0f} Long Call"
                    else:
                        # 6. Outright Long Put
                        m_lp = re.search(
                            r"(?:Long Put|Protective Put|Buy Put)[^\$]*\$?([0-9,.]+)\s*Put",
                            clean_combined,
                            re.IGNORECASE,
                        )
                        if m_lp:
                            options_struct = "LONG_PUT"
                            long_strike = _safe_float(m_lp.group(1))
                            opt_summary = f"${long_strike:.0f} Long Put"
                        else:
                            # 7. Cash-Secured Put
                            m_csp = re.search(
                                r"(?:Cash[- ]Secured Put|CSP|Short Put)[^\$]*\$?([0-9,.]+)\s*P?",
                                clean_combined,
                                re.IGNORECASE,
                            )
                            if m_csp:
                                options_struct = "CASH_SECURED_PUT"
                                short_strike = _safe_float(m_csp.group(1))
                                opt_summary = f"${short_strike:.0f} Cash-Secured Put"

    m_debit = (
        re.search(r"Net debit\s*[≈~]?\s*\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
        or re.search(r"Target entry:\s*<\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
        or re.search(r"Estimated Cost:\s*[≈~]?\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
    )
    if m_debit:
        target_debit = _safe_float(m_debit.group(1).rstrip("."))

    target_credit = 0.0
    m_credit = (
        re.search(r"Net credit\s*[≈~]?\s*\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
        or re.search(r"Target credit:\s*>?\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
        or re.search(r"Estimated Credit:\s*[≈~]?\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
    )
    if m_credit:
        target_credit = _safe_float(m_credit.group(1).rstrip("."))

    m_loss = re.search(r"Max Loss:?\s*\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
    if m_loss:
        max_loss = _safe_float(m_loss.group(1).rstrip("."))

    m_profit = re.search(r"Max Profit:?\s*\$?([0-9,.]+)", clean_combined, re.IGNORECASE)
    if m_profit:
        max_profit = _safe_float(m_profit.group(1).rstrip("."))

    # EM-consistency backstop: a credit spread is only "actionable" (i.e. promotable to
    # primary) when its short strike sits beyond 1.25x the Expected Move. If the LLM emitted
    # a high-IV credit on an unscaled strike, demote it so equity/debit can take the lead
    # instead of being buried behind a structurally weak spread.
    em_pct = _dw_lookup(dw_data, "Exp Move % (21b)", "exp_move_pct", "Exp Move Pct 21b")
    options_struct, opt_summary, clear_strikes = _demote_unscaled_credit(
        options_struct, short_strike, spot_price, em_pct, opt_summary, safe_ticker
    )
    if clear_strikes:
        long_strike = 0.0
        short_strike = 0.0
        # Zero stale P/L fields so UI doesn't render phantom risk/profit numbers
        target_debit = 0.0
        target_credit = 0.0
        max_loss = 0.0
        max_profit = 0.0

    # Invalidation
    invalidation_level = tactical_stop
    invalidation_cond = "DAILY_CLOSE_BELOW" if side == "LONG" else "DAILY_CLOSE_ABOVE"
    invalidation_rat = "Breaks key structural support" if side == "LONG" else "Breaks key structural resistance"

    m_inv = re.search(r"Daily Close below\s*\*\*?\$([0-9,.]+)\*\*?", arbitration_text)
    if m_inv:
        invalidation_level = _safe_float(m_inv.group(1))

    m_inv_rat = re.search(r"Logic:\*\s*([^.\n]+)", arbitration_text)
    if m_inv_rat:
        invalidation_rat = m_inv_rat.group(1).strip()

    # Initial State: spot 0.0 no longer invalidates
    initial_status = "STALKING"
    if invalidation_level and spot_price > 0:
        if side == "LONG" and spot_price < invalidation_level:
            initial_status = "INVALIDATED"
        elif side == "SHORT" and spot_price > invalidation_level:
            initial_status = "INVALIDATED"
    elif entry_low and entry_high and spot_price > 0 and (entry_low <= spot_price <= entry_high):
        initial_status = "IN_ZONE"

    result = {
        "ticker": safe_ticker,
        "date": date_str,
        "source": "fallback",
        "side": side,
        "spot_price": spot_price,
        "verdict": verdict,
        "conviction": conviction,
        "actionable": verdict in ("STALK", "ENTER", "PASS"),
        "shares_plan": {
            "entry_type": entry_type,
            "side": side,
            "entry_zone_low": entry_low,
            "entry_zone_high": entry_high,
            "proximity_buffer_pct": 1.0,
            "breakout_level": breakout_level,
            "breakout_stop": breakout_stop,
            "tactical_stop": tactical_stop,
            "target_1": target_1,
            "target_2": target_2,
            "rr_ratio": round(
                (target_1 - entry_high) / (entry_high - tactical_stop), 4
            ) if (target_1 and entry_high and tactical_stop and entry_high > tactical_stop) else 0.0,
            "allocation_pct": 25.0,
        },
        "options_plan": {
            "structure": options_struct,
            "expiration": exp_date,
            "long_strike": long_strike,
            "short_strike": short_strike,
            "target_debit": target_debit,
            "target_credit": target_credit,
            "max_loss": max_loss,
            "max_profit": max_profit,
            "summary": opt_summary,
            "actionable": options_struct not in ("NONE", "", None),
            "entry_trigger": "AT_FLOOR_LIMIT" if options_struct not in ("NONE", "", None) else "NONE",
        },
        "options_menu": {
            "tactical_spread": {
                "structure": options_struct,
                "expiration": exp_date,
                "long_strike": long_strike,
                "short_strike": short_strike,
                "target_debit": target_debit,
                "target_credit": target_credit,
                "summary": opt_summary,
            },
            "leaps": {
                "structure": "LONG_CALL" if re.search(r"(?:LEAPS|Long Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE) else "NONE",
                "long_strike": _safe_float(re.search(r"(?:LEAPS|Long Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE).group(1)) if re.search(r"(?:LEAPS|Long Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE) else 0.0,
                "summary": "Deep ITM LEAPS Call" if re.search(r"(?:LEAPS|Long Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE) else "No LEAPS specified",
            },
            "income_or_csp": {
                "structure": "COVERED_CALL" if re.search(r"(?:Covered Call|Sell Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE) else ("CASH_SECURED_PUT" if re.search(r"(?:Cash[- ]Secured Put|CSP|Short Put)[^\$]*\$?([0-9,.]+)\s*P?", clean_combined, re.IGNORECASE) else "NONE"),
                "short_strike": _safe_float(re.search(r"(?:Covered Call|Sell Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE).group(1)) if re.search(r"(?:Covered Call|Sell Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE) else (_safe_float(re.search(r"(?:Cash[- ]Secured Put|CSP|Short Put)[^\$]*\$?([0-9,.]+)\s*P?", clean_combined, re.IGNORECASE).group(1)) if re.search(r"(?:Cash[- ]Secured Put|CSP|Short Put)[^\$]*\$?([0-9,.]+)\s*P?", clean_combined, re.IGNORECASE) else 0.0),
                "summary": "Income / Put floor structure" if (re.search(r"(?:Covered Call|Sell Call)[^\$]*\$?([0-9,.]+)\s*Call", clean_combined, re.IGNORECASE) or re.search(r"(?:Cash[- ]Secured Put|CSP|Short Put)[^\$]*\$?([0-9,.]+)\s*P?", clean_combined, re.IGNORECASE)) else "No income / CSP structure specified",
            },
        },
        "invalidation": {
            "condition": invalidation_cond,
            "price_level": invalidation_level,
            "rationale": invalidation_rat,
        },
        "status": initial_status,
    }

    from src.logic.level_validation import validate_levels
    val_plan = {
        **(result.get("shares_plan") or {}),
        "options_plan": dict(result.get("options_plan") or {}),
        "ticker": safe_ticker,
        "date": date_str,
        "side": side,
        "setup_lane": result.get("setup_lane") or dw_data.get("setup_lane") or "WATCH_SHADOW",
        "spot": spot_price,
    }
    v_ok, v_reasons = validate_levels(val_plan, dw_data, side, ticker=safe_ticker, date_str=date_str)
    result["level_gate_rejected"] = not v_ok
    result["gate_reasons"] = v_reasons

    save_path = raw_dir / f"{safe_ticker}_watch_levels.json"
    if not save_path.exists():
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        logger.info(f"[{safe_ticker}] {save_path.name} already exists — preserving existing file without overwrite.")
    return result
