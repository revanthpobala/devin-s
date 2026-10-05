"""
src/logic/morning_briefing.py

Morning Executive Briefing & Opportunity Synthesis Engine.
Runs daily at 7:45 AM MT (or on-demand via UI).
Synthesizes rolling deep research history (evening, overnight, active 7-day horizon)
with updated real-time quotes to rank actionable opportunities.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config
from src.clients.price_client import get_current_prices_batch, get_realtime_quote_data
from src.logic.report_level_extractor import extract_watch_levels_from_report

logger = logging.getLogger(__name__)


def _determine_vintage(report_file: Path, report_date_str: str, now_mt: datetime) -> str:
    """Classify the research vintage into human-readable badges."""
    try:
        mtime = datetime.fromtimestamp(report_file.stat().st_mtime, tz=timezone.utc)
        mtime_mt = mtime.astimezone(ZoneInfo("America/Denver"))
        today_mt_str = now_mt.strftime("%Y-%m-%d")
        yesterday_mt_str = (now_mt - timedelta(days=1)).strftime("%Y-%m-%d")

        if mtime_mt.strftime("%Y-%m-%d") == today_mt_str:
            if mtime_mt.hour < 7:
                return f"⚡ Overnight ({mtime_mt.strftime('%I:%M %p').lstrip('0')} MT)"
            return f"🌅 Morning ({mtime_mt.strftime('%I:%M %p').lstrip('0')} MT)"
        elif mtime_mt.strftime("%Y-%m-%d") == yesterday_mt_str:
            if mtime_mt.hour >= 14:
                return f"🌙 Prior Evening ({mtime_mt.strftime('%I:%M %p').lstrip('0')} MT)"
            return f"📅 Yesterday ({mtime_mt.strftime('%b %d')})"
        else:
            days_ago = (now_mt.date() - mtime_mt.date()).days
            return f"📅 {mtime_mt.strftime('%b %d')} ({days_ago}d ago)"
    except Exception:
        return f"📅 {report_date_str}"


def _extract_pm_catalyst_bullets(arbitration_path: Path, max_bullets: int = 2) -> List[str]:
    """Extract top PM Arbitration key takeaways / catalysts from markdown."""
    if not arbitration_path.exists():
        return []
    try:
        text = arbitration_path.read_text(encoding="utf-8")
        bullets = []

        # 1. Look for THE CASE FOR section
        case_for_match = re.search(r"##\s*🟢\s*THE CASE FOR[\s\S]*?(?=##|\Z)", text, re.IGNORECASE)
        if case_for_match:
            case_text = case_for_match.group(0)
            items = re.findall(r"^\d+\.\s*\*\*([^*]+)\*\*:?\s*([^\n]+)", case_text, re.MULTILINE)
            for title, desc in items:
                b = f"<b>{title.strip()}</b>: {desc.strip()}"
                bullets.append(b)
                if len(bullets) >= max_bullets:
                    return bullets

        # 2. Look for JUDGE'S FINAL RULING or Concurrence
        ruling_match = re.search(r"##\s*⚖️\s*THE JUDGE'S FINAL RULING[\s\S]*?(?=##|\Z)", text, re.IGNORECASE)
        if ruling_match and len(bullets) < max_bullets:
            ruling_text = ruling_match.group(0)
            items = re.findall(r"^\*\s*\*\*([^*]+)\*\*:?\s*([^\n]+)", ruling_text, re.MULTILINE)
            for title, desc in items:
                b = f"<b>{title.strip()}</b>: {desc.strip()}"
                bullets.append(b)
                if len(bullets) >= max_bullets:
                    return bullets

        # 3. Fallback: Take first 2 non-empty lines from arbitration after watch_levels block
        if not bullets:
            clean = re.sub(r"```[\s\S]*?```", "", text)
            for line in clean.splitlines():
                line = line.strip()
                if line.startswith(("#", ">", "---", "* **Final Verdict")):
                    continue
                if len(line) > 30:
                    bullets.append(line[:120] + "...")
                    if len(bullets) >= max_bullets:
                        break

        return bullets
    except Exception as e:
        logger.debug(f"Failed extracting PM bullets: {e}")
        return []


def collect_rolling_deep_research(lookback_days: int = 7) -> Dict[str, Dict[str, Any]]:
    """
    Scan reports/ across lookback_days and find the latest deep research for each unique ticker.
    Returns mapping: ticker -> { 'report_date': str, 'summary_path': Path, 'arbitration_path': Path, 'mtime': float }
    """
    reports_base = config.BASE_DIR / "reports"
    if not reports_base.exists():
        return {}

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    date_dirs = []
    for d in reports_base.iterdir():
        if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name):
            try:
                dt = datetime.strptime(d.name, "%Y-%m-%d")
                date_dirs.append((dt, d))
            except Exception:
                continue

    # Sort descending by date
    date_dirs.sort(key=lambda x: x[0], reverse=True)

    candidates: Dict[str, Dict[str, Any]] = {}

    for dt, d_path in date_dirs[:lookback_days]:
        date_str = d_path.name
        for arb_file in d_path.glob("*_arbitration.md"):
            ticker = arb_file.name.replace("_arbitration.md", "").upper()
            if ticker in candidates:
                continue  # Already captured latest report
            sum_file = d_path / f"{ticker}_summary.md"

            # watch_levels.json: arbitration.py writes to raw/<date>/<ticker>/ (pass) or
            # reports/<date>/ (gate-rejected). Check both; raw dir wins as it's more complete.
            raw_watch = config.BASE_DIR / "data" / "raw" / date_str / ticker / f"{ticker}_watch_levels.json"
            rep_watch = d_path / f"{ticker}_watch_levels.json"
            if raw_watch.exists():
                watch_json = raw_watch
            elif rep_watch.exists():
                watch_json = rep_watch
            else:
                watch_json = None

            mtime = arb_file.stat().st_mtime
            vintage = _determine_vintage(arb_file, date_str, now_mt)

            candidates[ticker] = {
                "ticker": ticker,
                "report_date": date_str,
                "arbitration_path": arb_file,
                "summary_path": sum_file if sum_file.exists() else None,
                "watch_json_path": watch_json,
                "mtime": mtime,
                "vintage": vintage,
            }

    return candidates


def generate_morning_briefing(
    target_date: Optional[str] = None,
    force_live_quotes: bool = True,
    lookback_days: int = 7,
) -> Dict[str, Any]:
    """
    Synthesize all active deep research setups, enrich with live morning quotes,
    score, and generate the Morning Executive Briefing.
    """
    now_mt = datetime.now(ZoneInfo("America/Denver"))
    date_str = target_date or now_mt.strftime("%Y-%m-%d")

    logger.info(f"🌅 Generating Morning Executive Briefing for {date_str} (Lookback: {lookback_days}d)...")

    # 1. Collect candidate reports
    raw_candidates = collect_rolling_deep_research(lookback_days=lookback_days)
    if not raw_candidates:
        logger.warning("No deep research reports found in lookback window.")
        empty_res = {
            "briefing_date": date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_researched": 0,
            "tier1_count": 0,
            "tier2_count": 0,
            "tier1_actionable": [],
            "tier2_stalking": [],
            "tier3_monitor": [],
            "summary_headline": "No active deep research reports found.",
        }
        return empty_res

    tickers = sorted(list(raw_candidates.keys()))

    # 2. Batch-fetch live morning quotes
    quotes: Dict[str, float] = {}
    if force_live_quotes:
        try:
            quotes = get_current_prices_batch(tickers, context="surveillance")
        except Exception as e:
            logger.warning(f"Batch price fetch warning: {e}")

    # 3. Parse and evaluate each candidate setup
    evaluated_setups: List[Dict[str, Any]] = []

    for ticker, info in raw_candidates.items():
        rep_date = info["report_date"]
        arb_path = info["arbitration_path"]

        # Parse watch levels
        watch_data = None
        if info.get("watch_json_path") and info["watch_json_path"].exists():
            try:
                watch_data = json.loads(info["watch_json_path"].read_text(encoding="utf-8"))
            except Exception:
                pass
        if not watch_data:
            watch_data = extract_watch_levels_from_report(ticker, rep_date)

        if not watch_data:
            continue

        shares_p = watch_data.get("shares_plan") or {}
        options_p = watch_data.get("options_plan") or {}
        side = (watch_data.get("side") or shares_p.get("side") or "LONG").upper()
        verdict = (watch_data.get("verdict") or "STALK").upper()
        conviction = int(watch_data.get("conviction") or 5)

        entry_low = float(shares_p.get("entry_zone_low") or 0.0)
        entry_high = float(shares_p.get("entry_zone_high") or 0.0)
        tactical_stop = float(shares_p.get("tactical_stop") or 0.0)
        target_1 = float(shares_p.get("target_1") or 0.0)
        target_2 = float(shares_p.get("target_2") or 0.0)
        breakout_lvl = float(shares_p.get("breakout_level") or 0.0)

        # Check level gate rejection in text
        arb_text = arb_path.read_text(encoding="utf-8") if arb_path.exists() else ""
        level_gate_rejected = "LEVEL GATE REJECTED" in arb_text or "NO_LEVELS" in arb_text

        # Live quote evaluation
        spot = quotes.get(ticker)
        if not spot or spot <= 0:
            # Fallback to single quote or data window close
            try:
                qd = get_realtime_quote_data(ticker)
                if qd and qd.get("last_price"):
                    spot = float(qd["last_price"])
            except Exception:
                pass

        if not spot:
            spot = float(shares_p.get("last_price") or entry_high or 1.0)

        # Distance to entry zone
        dist_pct = 0.0
        in_zone = False
        if entry_low > 0 and entry_high > 0:
            if entry_low <= spot <= entry_high:
                dist_pct = 0.0
                in_zone = True
            elif spot > entry_high:
                dist_pct = round(((spot - entry_high) / entry_high) * 100.0, 2)
            else:
                dist_pct = round(((spot - entry_low) / entry_low) * 100.0, 2)

        # Live R:R calculation
        live_rr = 0.0
        if side == "LONG":
            risk = spot - tactical_stop
            reward = target_1 - spot
            if risk > 0 and reward > 0:
                live_rr = round(reward / risk, 2)
        else:
            risk = tactical_stop - spot
            reward = spot - target_1
            if risk > 0 and reward > 0:
                live_rr = round(reward / risk, 2)

        # State classification
        if side == "LONG" and tactical_stop > 0 and spot <= tactical_stop:
            state = "STOP_BREACHED"
        elif side == "SHORT" and tactical_stop > 0 and spot >= tactical_stop:
            state = "STOP_BREACHED"
        elif side == "LONG" and target_1 > 0 and spot >= target_1:
            state = "TARGET_HIT"
        elif side == "SHORT" and target_1 > 0 and spot <= target_1:
            state = "TARGET_HIT"
        elif in_zone:
            state = "IN_ZONE"
        elif abs(dist_pct) <= 1.5:
            state = "STALKING_NEAR"
        else:
            state = "STALKING"

        # PM Catalyst bullets
        pm_bullets = _extract_pm_catalyst_bullets(arb_path, max_bullets=2)

        # Opportunity Scoring Engine (0 - 100)
        score = conviction * 8  # 5 -> 40, 8 -> 64
        if in_zone:
            score += 30
        elif abs(dist_pct) <= 1.5:
            score += 15
        elif abs(dist_pct) <= 3.0:
            score += 5

        if live_rr >= 2.5:
            score += 15
        elif live_rr >= 2.0:
            score += 10

        # Options bonus if defined risk with positive expectation
        opt_struct = (options_p.get("structure") or "NONE").upper()
        if opt_struct not in ("NONE", "EMPTY", "–"):
            max_profit = float(options_p.get("max_profit") or 0.0)
            max_loss = float(options_p.get("max_loss") or 0.0)
            if max_profit > 0 and max_loss > 0 and max_profit >= max_loss:
                score += 10
            else:
                score += 5

        # Penalties & Warnings
        gate_warning = ""
        if level_gate_rejected:
            score -= 15
            gate_warning = "Gate Alert: Strict PM stop/RR warning"
        if state == "STOP_BREACHED":
            score -= 50
        elif state == "TARGET_HIT":
            score -= 20
        elif abs(dist_pct) > 5.0:
            score -= 20

        score = max(0, min(100, score))

        # Vehicle summary formatting
        has_options = opt_struct not in ("NONE", "EMPTY", "–") and options_p.get("actionable") is not False
        long_k = options_p.get("long_strike")
        short_k = options_p.get("short_strike")
        exp_date = options_p.get("expiration")
        target_debit = options_p.get("target_debit")
        target_credit = options_p.get("target_credit")
        opt_summary = options_p.get("summary") or ""

        vehicle_type = "OPTIONS" if (has_options and opt_struct != "NONE") else "SHARES"
        vehicle_label = ""
        if vehicle_type == "OPTIONS":
            cost_str = f"Debit ~${float(target_debit):.2f}" if target_debit else (f"Credit ~${float(target_credit):.2f}" if target_credit else "")
            strikes_str = f"${long_k}C / ${short_k}C" if "CALL" in opt_struct else f"${short_k}P / ${long_k}P"
            vehicle_label = f"{opt_struct.replace('_', ' ')} ({strikes_str}) · Exp: {exp_date} · {cost_str}".strip(" ·")
        else:
            vehicle_label = f"EQUITY SHARES · Entry: ${entry_low:.2f}–${entry_high:.2f} · Stop: ${tactical_stop:.2f} · T1: ${target_1:.2f}"

        measured_actionable = bool(
            in_zone
            and live_rr >= 2.0
            and not level_gate_rejected
            and state not in ("STOP_BREACHED", "TARGET_HIT", "INVALIDATED")
        )

        # Assign Tier
        if measured_actionable:
            tier = "TIER_1_ACTIONABLE"
        elif score >= 45 and state != "STOP_BREACHED":
            tier = "TIER_2_STALKING"
        else:
            tier = "TIER_3_MONITOR"

        setup_entry = {
            "ticker": ticker,
            "measured_actionable": measured_actionable,
            "report_date": rep_date,
            "vintage": info["vintage"],
            "side": side,
            "verdict": verdict,
            "conviction": conviction,
            "score": score,
            "tier": tier,
            "state": state,
            "spot_price": spot,
            "dist_pct": dist_pct,
            "in_zone": in_zone,
            "live_rr": live_rr,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "tactical_stop": tactical_stop,
            "target_1": target_1,
            "target_2": target_2,
            "breakout_level": breakout_lvl,
            "level_gate_rejected": level_gate_rejected,
            "gate_warning": gate_warning,
            "vehicle_type": vehicle_type,
            "vehicle_label": vehicle_label,
            "options_plan": options_p,
            "pm_bullets": pm_bullets,
            "invalidation": watch_data.get("invalidation") or {},
        }
        evaluated_setups.append(setup_entry)

    # Sort setups by score descending
    evaluated_setups.sort(key=lambda s: s["score"], reverse=True)

    tier1 = [s for s in evaluated_setups if s["tier"] == "TIER_1_ACTIONABLE"]
    tier2 = [s for s in evaluated_setups if s["tier"] == "TIER_2_STALKING"]
    tier3 = [s for s in evaluated_setups if s["tier"] == "TIER_3_MONITOR"]

    headline = f"Synthesized {len(evaluated_setups)} active setups from rolling deep research. {len(tier1)} top actionable opportunities in play."

    result = {
        "briefing_date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_researched": len(evaluated_setups),
        "tier1_count": len(tier1),
        "tier2_count": len(tier2),
        "tier3_count": len(tier3),
        "summary_headline": headline,
        "tier1_actionable": tier1,
        "tier2_stalking": tier2,
        "tier3_monitor": tier3,
        "all_setups": evaluated_setups,
    }

    # Save cache files
    briefing_dir = config.BASE_DIR / "data" / "briefings"
    briefing_dir.mkdir(parents=True, exist_ok=True)
    json_path = briefing_dir / f"briefing_{date_str}.json"
    md_path = briefing_dir / f"briefing_{date_str}.md"

    try:
        json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        _write_markdown_briefing(result, md_path)
        logger.info(f"✅ Morning Executive Briefing saved to {json_path} and {md_path}")
    except Exception as e:
        logger.error(f"Failed writing briefing cache: {e}")

    return result


def _write_markdown_briefing(result: Dict[str, Any], out_path: Path):
    """Write executive readable markdown version of the briefing."""
    date_str = result.get("briefing_date", "")
    lines = [
        f"# 🌅 MORNING EXECUTIVE INTELLIGENCE & ACTIONABLE OPPORTUNITIES",
        f"**Date:** {date_str} | **Generated At:** {result.get('generated_at', '')} | **Universe:** {result.get('total_researched', 0)} Active Deep Research Setups\n",
        f"## 🏆 TIER 1: PRIME ACTIONABLE OPPORTUNITIES ({result.get('tier1_count', 0)})",
    ]

    for s in result.get("tier1_actionable", []):
        sym = s["ticker"]
        dist = f"{s['dist_pct']:+.1f}%" if not s["in_zone"] else "🎯 IN ZONE"
        lines.append(f"### ${sym} · {s['side']} · {s['state']} ({dist})")
        lines.append(f"- **Vintage:** {s['vintage']} | **Conviction:** {s['conviction']}/10 | **Score:** {s['score']}/100 | **Spot:** ${s['spot_price']:.2f} | **Live R:R:** {s['live_rr']}:1")
        lines.append(f"- **Execution Vehicle:** {s['vehicle_label']}")
        if s.get("pm_bullets"):
            lines.append("- **Senior PM Thesis:**")
            for b in s["pm_bullets"]:
                lines.append(f"  • {b}")
        lines.append("")

    lines.append(f"## ⏳ TIER 2: COILED STALKING SETUPS ({result.get('tier2_count', 0)})")
    for s in result.get("tier2_stalking", []):
        sym = s["ticker"]
        lines.append(f"- **${sym}** ({s['vintage']}): Spot ${s['spot_price']:.2f} ({s['dist_pct']:+.1f}% from entry) · {s['vehicle_label']}")

    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
