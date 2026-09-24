"""
Trade Ideas Generator for Institutional Watchlist & Open Positions.
Generates structured, high-conviction informational trade ideas for:
1. Open Positions (Position Management, Profit Harvesting, Covered Calls, Stop Defense)
2. Stalking & In-Zone Candidates (Limit Execution, Options Spreads, R:R Math, Invalidation)
"""

from typing import Dict, List, Any, Optional


def generate_trade_ideas_for_target(target: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    ideas = []
    sym = (target.get("ticker") or "").upper()
    spot = float(target.get("last_price") or target.get("spot_price") or 0.0)

    # Check if this target is an active position
    is_open = bool(position and (position.get("has_position") or position.get("quantity") or position.get("average_price") or position.get("qty"))) or (target.get("status") == "IN_TRADE") or bool(target.get("is_open_position"))

    if is_open:
        pos_data = position or target.get("position") or {}
        qty = int(pos_data.get("quantity") or 1)
        raw_entry = pos_data.get("average_price") or target.get("entry_price")
        if not raw_entry:
            return []
        try:
            entry = float(raw_entry)
            if entry <= 0:
                return []
        except (ValueError, TypeError):
            return []
        side = str(pos_data.get("side") or target.get("side") or "LONG").upper()
        stop = float(target.get("tactical_stop") or pos_data.get("stop") or 0.0)
        t1 = float(target.get("target_1") or pos_data.get("target") or 0.0)
        t2 = float(target.get("target_2") or 0.0)
        opt_plan = target.get("options_summary") or target.get("options_structure") or ""

        # 1. Scale-Out & Profit Taking Idea (only if valid target and stop exist)
        if t1 > 0 and stop > 0:
            scale_qty = max(1, qty // 2) if qty >= 2 else qty
            rem_qty = qty - scale_qty
            if "LONG" in side:
                t1_gain = (t1 - entry) * scale_qty
                t1_pct = ((t1 - entry) / entry * 100) if entry > 0 else 0.0
                t2_gain = (t2 - entry) * rem_qty if (rem_qty > 0 and t2 > 0) else 0.0
                t2_pct = ((t2 - entry) / entry * 100) if (entry > 0 and t2 > 0) else 0.0

                if qty >= 2:
                    scale_desc = (
                        f"Set GTC Limit Sell for {scale_qty} share{'s' if scale_qty > 1 else ''} at Target 1 (${t1:.2f}) "
                        f"to lock in +${t1_gain:.2f} profit (+{t1_pct:.2f}%). "
                        f"Upon fill, immediately advance protective stop on remaining {rem_qty} share{'s' if rem_qty > 1 else ''} "
                        f"to breakeven (${entry:.2f})"
                        + (f" to run risk-free into Target 2 (${t2:.2f}, +{t2_pct:.2f}% / +${t2_gain:.2f})." if t2 > 0 else ".")
                    )
                else:
                    scale_desc = (
                        f"Target 1 sits at ${t1:.2f} (+{t1_pct:.2f}%). "
                        f"Consider taking profits or trailing stop close to spot upon testing this primary resistance zone."
                    )
            else:
                # SHORT
                t1_gain = (entry - t1) * scale_qty
                t1_pct = ((entry - t1) / entry * 100) if entry > 0 else 0.0
                scale_desc = (
                    f"Cover {scale_qty} share{'s' if scale_qty > 1 else ''} at Target 1 (${t1:.2f}) "
                    f"to bank +${t1_gain:.2f} (+{t1_pct:.2f}%). Advance stop on remainder to entry (${entry:.2f})."
                )

            ideas.append({
                "id": "scale_out",
                "category": "RISK MANAGEMENT",
                "badge": "SCALE OUT 50%" if qty >= 2 else "TAKE PROFIT",
                "color": "emerald",
                "title": f"Scale Profit at Target 1 (${t1:.2f})",
                "action": scale_desc,
                "metrics": f"T1: ${t1:.2f} (+{t1_pct:.2f}%) | Stop: ${stop:.2f}",
                "rationale": f"T1: ${t1:.2f} (+{t1_pct:.2f}%) | Stop: ${stop:.2f}"
            })

        # 2. Capital Defense / Stop Floor (only if valid stop exists)
        if stop > 0:
            risk_per_sh = abs(entry - stop)
            tot_risk = risk_per_sh * qty
            risk_pct = (risk_per_sh / entry * 100) if entry > 0 else 0.0

            ideas.append({
                "id": "capital_defense",
                "category": "RISK DEFENSE",
                "badge": "CAPITAL FLOOR",
                "color": "rose",
                "title": f"Tactical Stop Defense at ${stop:.2f}",
                "action": (
                    f"Maintain protective stop at ${stop:.2f} (-{risk_pct:.2f}% from entry). "
                    f"Total max capital at risk across {qty} share{'s' if qty > 1 else ''} is capped at ${tot_risk:.2f} (${risk_per_sh:.2f}/share). "
                    f"Exit position if a 1D candle closes below ${stop:.2f}."
                ),
                "metrics": f"Risk Floor: ${stop:.2f} (-{risk_pct:.2f}%) | Max Risk: ${tot_risk:.2f}",
                "rationale": f"Risk Floor: ${stop:.2f} (-{risk_pct:.2f}%) | Max Risk: ${tot_risk:.2f}"
            })

        # 3. Options Monetization & Yield Idea
        strike_desc = f"~${t2:.2f}" if t2 > 0 else f"above ${entry:.2f}"
        if qty >= 100:
            opt_title = "Covered Call Income Generation"
            opt_action = (
                f"Holding {qty} shares allows selling standard covered calls. "
                f"Sell 1x 30-45 DTE Out-of-the-Money Call (suggested strike {strike_desc}) "
                f"to generate ~1.5%-2.5% annualized cashflow yield while preserving upside."
            )
            opt_badge = "COVERED CALL"
        else:
            opt_title = "Options Monetization / Defined Spread"
            t1_target_str = f"targeting ${t1:.2f}" if t1 > 0 else "targeting resistance"
            opt_action = (
                f"Holding {qty} share{'s' if qty > 1 else ''} (odd-lot). Standard covered calls require 100 shares. "
                f"To monetize or leverage this move without 100 shares: (1) Consider a defined-risk Bull Call Spread ({opt_plan or f'Call Spread {t1_target_str}'}), "
                f"or (2) accumulate to 100 shares for regular covered call income."
            )
            opt_badge = "ODD-LOT PLAY"

        ideas.append({
            "id": "options_yield",
            "category": "OPTIONS YIELD",
            "badge": opt_badge,
            "color": "cyan",
            "title": opt_title,
            "action": opt_action,
            "metrics": opt_plan or "Yield Strategy",
            "rationale": opt_plan or "Yield Strategy"
        })

        # 4. Retest / Scale-in on Pullback (only if real entry zone and stop exist)
        ez_low = float(target.get("entry_zone_low") or 0.0)
        ez_high = float(target.get("entry_zone_high") or 0.0)
        if ez_low > 0 and ez_high > 0 and stop > 0:
            ideas.append({
                "id": "scale_in",
                "category": "ACCUMULATION",
                "badge": "DIP BUY ZONE",
                "color": "blue",
                "title": f"Demand Retest Pocket (${ez_low:.2f} - ${ez_high:.2f})",
                "action": (
                    f"If price re-tests the ${ez_low:.2f}-${ez_high:.2f} demand pocket and forms a reversal candle with volume absorption, "
                    f"opportunity to scale in an additional {max(1, qty // 2)} share{'s' if (qty // 2) > 1 else ''} "
                    f"using the exact same ${stop:.2f} tactical stop."
                ),
                "metrics": f"Zone: ${ez_low:.2f}-${ez_high:.2f} | Support: ${stop:.2f}",
                "rationale": f"Zone: ${ez_low:.2f}-${ez_high:.2f} | Support: ${stop:.2f}"
            })

    else:
        # STALKING / IN-ZONE CANDIDATE
        ez_low = float(target.get("entry_zone_low") or 0.0)
        ez_high = float(target.get("entry_zone_high") or 0.0)
        stop = float(target.get("tactical_stop") or 0.0)
        t1 = float(target.get("target_1") or 0.0)
        t2 = float(target.get("target_2") or 0.0)
        opt_summary = target.get("options_summary") or "Defined-risk call debit spread"

        if ez_low <= 0 or ez_high <= 0 or stop <= 0 or t1 <= 0 or stop >= ez_low or ez_high >= t1:
            ideas.append({
                "id": "invalid_geometry",
                "category": "INVALID GEOMETRY",
                "badge": "INVALID SETUP",
                "color": "amber",
                "title": "Invalid or Incomplete Trade Levels",
                "action": "Deterministic trade levels are missing or invalid (stop >= entry or entry >= target). No trade recommended.",
                "metrics": f"Entry: ${ez_low:.2f}–${ez_high:.2f} | Stop: ${stop:.2f} | Target: ${t1:.2f}",
                "rationale": "Level gate requires valid stop < entry_low <= entry_high < target_1.",
            })
            return ideas

        risk_per_sh = abs(ez_high - stop)
        reward_per_sh = abs(t1 - ez_high)
        rr_ratio = (reward_per_sh / risk_per_sh) if risk_per_sh > 0 else 0.0

        ideas.append({
            "id": "limit_entry",
            "category": "ENTRY SETUP",
            "badge": "LIMIT BUY",
            "color": "emerald",
            "title": f"Resting Limit Buy in Zone (${ez_low:.2f} – ${ez_high:.2f})",
            "action": (
                f"Place resting GTC Limit Buy order between ${ez_low:.2f} and ${ez_high:.2f}. "
                f"Risk ${risk_per_sh:.2f}/sh to capture ${reward_per_sh:.2f}/sh upside to Target 1. "
                f"Risk/Reward ratio is 1 : {rr_ratio:.2f}."
            ),
            "metrics": f"Zone: ${ez_low:.2f}–${ez_high:.2f} | R:R 1:{rr_ratio:.2f}",
            "rationale": f"Zone: ${ez_low:.2f}–${ez_high:.2f} | R:R 1:{rr_ratio:.2f}"
        })

        ideas.append({
            "id": "options_spread",
            "category": "LEVERAGED VEHICLE",
            "badge": "DEFINED RISK",
            "color": "cyan",
            "title": f"Options Structure: {target.get('options_structure') or 'Bull Call Spread'}",
            "action": opt_summary,
            "metrics": f"Target 1: ${t1:.2f} | Target 2: ${t2:.2f}",
            "rationale": f"Target 1: ${t1:.2f} | Target 2: ${t2:.2f}"
        })

        ideas.append({
            "id": "invalidation",
            "category": "RISK MANAGEMENT",
            "badge": "INVALIDATION",
            "color": "amber",
            "title": f"Cancel & Invalidate below ${stop:.2f}",
            "action": (
                f"If spot closes below ${stop:.2f} on a daily bar, the structural accumulation thesis is dead. "
                f"Cancel all resting limit orders."
            ),
            "metrics": f"Stop Level: ${stop:.2f}",
            "rationale": f"Stop Level: ${stop:.2f}"
        })

    return ideas
