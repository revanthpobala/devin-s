#!/usr/bin/env python3
"""
scripts/research/replay_runaway_rules.py — E1 from plan/no-lost-movers.md.

Replays each missed move under each candidate runaway rule and writes the corpus.
No target selection: every episode discovered by the missed-move scan gets replayed
under every rule, so a rule cannot be shown in its best light.

Rules replayed (the plan's three, each against the real plan levels and honest bar math):

  zone_top_market    (a) buy at market when price breaks above the zone top without a fill,
                         gated on R:R at market >= the floor.
  high_20d_break     (b) buy a 20-day-high break on volume pace: the break bar must close
                         above the prior 20-day high AND trade >= 1.0x ADV20.
  post_t1_reentry    (c) buy at market after T1 is reached without a fill, with a refreshed
                         stop (the lowest low in the run from the setup bar through the T1
                         bar — the structure that formed while price ran; never worse than
                         the plan's own stop).

Replay math is `evaluate_setup_lifecycle_bars` (src/tracking/execution_validator.py):
next-open entry, stop-first same-bar ambiguity, 21-bar walk. Costs are the frozen
suggestion_scorer model (cost_r = 0.0005 * (fill + exit) / risk), so every R is NET
by the same math the rest of the audit uses.

The in-sample corpus ends 2026-08-21 (--end-date); the forward log decides out-of-sample.
The three named fixtures (META 10-09, VST, NRG) always run regardless of window and land
under data/replay/runaway_fixtures.json — a fixture with no stored plan is recorded as
no_stored_plan with its reason rather than being replayed against invented levels.

Per rule the report prints the era split (2006-2015 vs 2016-2026), first/second halves,
ticker breadth, median net R, and the fire-rate, which must stay under 6% of bars.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src import config  # noqa: E402
from src.tracking.execution_validator import evaluate_setup_lifecycle_bars, get_bars_since_date  # noqa: E402
from src.tracking.rr_config import min_rr  # noqa: E402

logger = logging.getLogger("replay_runaway_rules")

# The plan's three rules (E1). Nothing else is replayed or claimed.
RULES = ("zone_top_market", "high_20d_break", "post_t1_reentry")

# The honest pass bar for this corpus, printed with every claim.
MIN_REPLAYS = 20
MIN_WIN_RATE = 0.45
MIN_EXPECTANCY = 0.0
MAX_HOLDING_BARS = 21
MAX_FIRE_RATE = 0.06
ERA_SPLIT_YEAR = "2016"  # repo convention (replay_data_window_filter.py): 2006-2015 vs 2016-2026

# The named fixtures the audit must run: META has a stored plan on disk, VST's prices come
# from the audit's BACKFILL record, and NRG's price pair is recorded with NO stored plan
# (it was cut by a real barrier, which is the root-cause finding — no levels to arm).
NAMED_FIXTURES = [
    {"ticker": "META", "dossier_date": "2026-10-09", "dossier_spot": 718.67, "live_spot": 748.10,
     "last_verdict": "CUT", "source": "fixture"},
    {"ticker": "VST", "dossier_date": "2026-10-02", "dossier_spot": 140.44, "live_spot": 166.66,
     "last_verdict": "WATCH", "source": "fixture"},
    {"ticker": "NRG", "dossier_date": "2026-10-05", "dossier_spot": 102.75, "live_spot": 109.22,
     "last_verdict": "WATCH", "source": "fixture", "no_stored_plan_reason": "cut_reason None (chased flag); root_cause stale_data"},
]


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bars_frame(ticker: str, start_date: str):
    try:
        return get_bars_since_date(ticker, start_date)
    except Exception as e:  # pragma: no cover - offline path
        logger.debug(f"[{ticker}] get_bars_since_date failed: {e}")
        return None


def _row_series(bars) -> List[Dict[str, float]]:
    """[(date, open, high, low, close, volume)] from a bars frame; [] when unusable."""
    out: List[Dict[str, float]] = []
    try:
        for i in range(len(bars)):
            row = bars.iloc[i]
            idx = [str(x)[:10] for x in bars.index]
            rec = {
                "date": idx[i],
                "open": _f(row.get("Open") if hasattr(row, "get") else None),
                "high": _f(row.get("High") if hasattr(row, "get") else None),
                "low": _f(row.get("Low") if hasattr(row, "get") else None),
                "close": _f(row.get("Close") if hasattr(row, "get") else None),
                "volume": _f(row.get("Volume") if hasattr(row, "get") else None),
            }
            if rec["close"] > 0:
                out.append(rec)
    except Exception as e:
        logger.debug(f"bar series parse failed: {e}")
    return out


def _prior_20d_high(series: List[Dict[str, float]], upto: str) -> Optional[float]:
    highs = [b["high"] for b in series if b["date"] <= upto and b["high"] > 0]
    return max(highs[-20:]) if highs else None


def _prior_adv20(series: List[Dict[str, float]], upto: str) -> Optional[float]:
    vols = [b["volume"] for b in series if b["date"] <= upto and b["volume"] > 0]
    tail = vols[-20:]
    return (sum(tail) / len(tail)) if tail else None


def _plan_from_watch_levels(chosen: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(chosen.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"dossier parse failed for {chosen}: {e}")
        return None
    sp = data.get("shares_plan") or {}
    return {
        "setup_date": str(chosen.parent.parent.name)[:10],
        "source": str(chosen),
        "entry_zone_low": sp.get("entry_zone_low") or (sp.get("entry_zone") or [0, 0])[0],
        "entry_zone_high": sp.get("entry_zone_high") or (sp.get("entry_zone") or [0, 0])[1],
        "breakout_level": sp.get("breakout_level") or 0.0,
        "tactical_stop": sp.get("tactical_stop") or 0.0,
        "target_1": sp.get("target_1") or 0.0,
        "target_2": sp.get("target_2") or 0.0,
    }


def _plan_from_thesis(thesis_path: Path) -> Optional[Dict[str, Any]]:
    """Plan levels from a stored local-triage thesis when no watch levels exist.

    Local-triage names never reach deep research, so they have no watch_levels.json —
    but their stored long_plan is exactly the plan the desk held, and replaying against
    it is honest. Reports its own date so a stale plan is visible, never hidden.
    """
    try:
        data = json.loads(thesis_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"thesis parse failed for {thesis_path}: {e}")
        return None
    triage = data.get("triage") if isinstance(data.get("triage"), dict) else data
    lp = (triage or {}).get("long_plan") or {}
    if not lp or not (_f(lp.get("stop")) > 0 and (_f(lp.get("target")) > 0 or _f(lp.get("target_1")) > 0)):
        return None
    zone = lp.get("zone") or [0, 0]
    parent = thesis_path.parent
    return {
        "setup_date": parent.parent.name if len(parent.parent.name) == 10 else str(thesis_path.parent.name)[:10],
        "source": str(thesis_path),
        "entry_zone_low": _f(zone[0]),
        "entry_zone_high": _f(zone[1]) if len(zone) > 1 else 0.0,
        "breakout_level": _f(lp.get("breakout_level")),
        "tactical_stop": _f(lp.get("stop")),
        "target_1": _f(lp.get("target") or lp.get("target_1")),
        "target_2": _f(lp.get("target_2")),
    }


def _latest_dossier_plan(ticker: str, dossier_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Plan levels from the ticker's most recent stored plan, preferring the named date.

    Prefers the deep-research watch levels; falls back to the local-triage thesis long_plan
    (the plan the desk actually held for names that never reached deep research). Both are
    stored plans; the fallback's date rides along so staleness is visible.
    """
    safe = ticker.replace(":", "_").upper()
    base = config.BASE_DIR / "data"
    watch_candidates: List[Path] = []
    thesis_candidates: List[Path] = []
    for sub in ("raw", "triage"):
        sub_root = base / sub
        if not sub_root.exists():
            continue
        watch_candidates.extend(sub_root.glob(f"*/{safe}/{safe}_watch_levels.json"))
        thesis_candidates.extend(sub_root.glob(f"*/{safe}/{safe}_thesis.json"))

    def prefers(paths: List[Path]) -> List[Path]:
        return [p for p in paths if dossier_date and str(p.parent.parent.name).startswith(str(dossier_date)[:10])]

    watch_pref = prefers(watch_candidates)
    if watch_pref:
        plan = _plan_from_watch_levels(watch_pref[0])
        if plan:
            return plan
    if watch_candidates:
        watch_candidates.sort(key=lambda p: str(p.parent.parent.name), reverse=True)
        plan = _plan_from_watch_levels(watch_candidates[0])
        if plan:
            return plan
    thesis_pref = prefers(thesis_candidates)
    if thesis_pref:
        plan = _plan_from_thesis(thesis_pref[0])
        if plan:
            return plan
    if thesis_candidates:
        thesis_candidates.sort(key=lambda p: str(p.parent.parent.name), reverse=True)
        return _plan_from_thesis(thesis_candidates[0])
    return None


def build_fixtures() -> List[Dict[str, Any]]:
    """The three named fixtures, with plans resolved from disk where a plan exists.

    NRG has no stored plan (it was cut by a real barrier), so it is carried as a
    documented no-op with its reason instead of being replayed against invented levels.
    """
    fixtures: List[Dict[str, Any]] = []
    for fx in NAMED_FIXTURES:
        row = dict(fx)
        plan = _latest_dossier_plan(fx["ticker"], fx["dossier_date"])
        if plan:
            row["plan"] = plan
            row["replayable"] = True
        else:
            row["plan"] = None
            row["replayable"] = False
            row.setdefault("no_stored_plan_reason", "no stored watch levels with real levels on disk")
        fixtures.append(row)
    return fixtures


def discover_episodes(
    end_date: str, max_episodes: int = 200
) -> List[Dict[str, Any]]:
    """Every replayable episode with a stored plan inside the in-sample window.

    Generalized discovery: any dossier on disk with real plan levels (deep-research watch
    levels or a local-triage thesis long_plan) and a setup date on or before `end_date`
    joins the corpus. The named fixtures are added by `build_fixtures` regardless of window.
    """
    base = config.BASE_DIR / "data"
    episodes: Dict[str, Dict[str, Any]] = {}
    if not base.exists():
        return []
    for sub in ("raw", "triage"):
        sub_root = base / sub
        if not sub_root.exists():
            continue
        for dt_dir in sub_root.iterdir():
            if not dt_dir.is_dir() or len(dt_dir.name) != 10:
                continue
            if dt_dir.name > end_date:
                continue
            for th_p in dt_dir.glob("*/*_thesis.json"):
                sym = th_p.name.replace("_thesis.json", "").upper()
                if sym in episodes:
                    continue
                plan = _plan_from_thesis(th_p)
                if not plan:
                    continue
                episodes[sym] = {
                    "ticker": sym,
                    **plan,
                    "source": "dossier_scan",
                    "last_verdict": "STALK",
                }
            for wl in dt_dir.glob("*/*_watch_levels.json"):
                sym = wl.name.replace("_watch_levels.json", "").upper()
                if sym in episodes:
                    continue
                plan = _plan_from_watch_levels(wl)
                if not plan or not (_f(plan.get("tactical_stop")) > 0 and _f(plan.get("target_1")) > 0):
                    continue
                episodes[sym] = {
                    "ticker": sym,
                    **plan,
                    "source": "dossier_scan",
                    "last_verdict": "STALK",
                }
    out = [e for e in episodes.values() if _f(e.get("tactical_stop")) > 0 and _f(e.get("target_1")) > 0]
    return out[:max_episodes]


def rule_signal(rule: str, plan: Dict[str, Any], series: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """The lifecycle kwargs + trigger facts for one rule on one plan.

    Returns None when the rule cannot be expressed or its trigger never occurs in the
    walk — an untriggered rule fires on zero bars, which is what keeps the fire-rate honest.
    """
    entry_high = _f(plan.get("entry_zone_high"))
    stop = _f(plan.get("tactical_stop"))
    t1 = _f(plan.get("target_1"))
    t2 = _f(plan.get("target_2"))
    setup_date = str(plan.get("setup_date") or "")
    walk = [b for b in series if b["date"] > setup_date][: MAX_HOLDING_BARS + 1]
    if not walk or stop <= 0 or t1 <= 0:
        return None

    if rule == "zone_top_market":
        # (a) market entry when price trades through the zone top, R:R at that print >= floor.
        if entry_high <= 0:
            return None
        for i, b in enumerate(walk):
            if b["close"] > entry_high or b["high"] > entry_high:
                px = max(b["open"], entry_high + 0.01)
                if t1 <= px or (t1 - px) / max(px - stop, 0.01) < min_rr():
                    continue
                return dict(entry_type="BREAKOUT", breakout_level=entry_high, stop_loss=stop,
                            target_1=t1, target_2=t2, trigger_index=i, trigger_px=px)
        return None

    if rule == "high_20d_break":
        # (b) the break bar closes above the prior 20-day high AND trades >= 1.0x ADV20.
        prior_high = _prior_20d_high(series, setup_date)
        adv = _prior_adv20(series, setup_date)
        if not prior_high or not adv:
            return None
        for i, b in enumerate(walk):
            if b["close"] > prior_high and b["volume"] >= adv:
                px = max(b["open"], prior_high + 0.01)
                if t1 <= px or px <= stop:
                    continue
                return dict(entry_type="BREAKOUT", breakout_level=prior_high, stop_loss=stop,
                            target_1=t1, target_2=t2, trigger_index=i, trigger_px=px,
                            rvol_on_break=round(b["volume"] / adv, 3))
        return None

    if rule == "post_t1_reentry":
        # (c) after T1 is reached without a fill, re-enter at market with a refreshed stop:
        # the lowest low in the run from the setup bar through the T1 bar (never worse
        # than the plan's own stop).
        t1_bar_idx = next((i for i, b in enumerate(walk) if b["high"] >= t1), None)
        if t1_bar_idx is None:
            return None
        refreshed_stop = min([b["low"] for b in walk[: t1_bar_idx + 1] if b["low"] > 0] + [stop])
        reentry_bar = walk[t1_bar_idx]
        px = max(reentry_bar["open"], t1)
        if px <= refreshed_stop or t2 <= px:
            return None
        return dict(entry_type="BREAKOUT", breakout_level=t1, stop_loss=refreshed_stop,
                    target_1=t2 or t1 * 1.02, target_2=None,
                    trigger_index=t1_bar_idx, trigger_px=px, refreshed_stop=refreshed_stop)
    return None


def _net_r(fill: float, exit_px: float, stop: float) -> Optional[float]:
    """R multiple with the frozen suggestion_scorer cost model applied."""
    if fill <= 0 or exit_px <= 0 or fill <= stop:
        return None
    risk = fill - stop
    gross = (exit_px - fill) / risk
    cost_r = (0.0005 * (fill + exit_px)) / risk
    return round(gross - cost_r, 4)


def replay_episode(rule: str, ep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Replay one rule on one episode; returns the corpus row or None."""
    setup_date = str(ep["setup_date"])
    bars = _bars_frame(ep["ticker"], (datetime.strptime(setup_date, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d"))
    if bars is None or len(bars) < 2:
        return None
    series = _row_series(bars)
    if len(series) < 2:
        return None
    sig = rule_signal(rule, ep, series)
    if sig is None:
        return None

    stop = _f(sig.get("stop_loss"))
    t1 = _f(sig.get("target_1"))
    t2 = _f(sig.get("target_2"))

    try:
        res = evaluate_setup_lifecycle_bars(
            bars=bars,
            setup_date=setup_date,
            side="LONG",
            live_price=_f(sig.get("trigger_px")),
            current_status="STALKING",
            max_holding_bars=MAX_HOLDING_BARS,
            entry_type=sig["entry_type"],
            breakout_level=_f(sig.get("breakout_level")),
            stop_loss=stop,
            target_1=t1,
            target_2=t2 if t2 > 0 else None,
        )
    except Exception as e:
        logger.debug(f"[{ep['ticker']}] {rule} replay failed: {e}")
        return None

    filled = bool(res.get("was_filled"))
    fill_px = _f(res.get("fill_price"))
    exit_px = _f(res.get("exit_price"))
    terminal = bool(res.get("is_terminal"))
    hit_t2 = bool(res.get("hit_t2"))
    hit_t1 = bool(res.get("hit_t1"))
    hit_stop = bool(res.get("hit_stop"))

    trigger_px = _f(sig.get("trigger_px"))
    rr_at_trigger = None
    if trigger_px > stop and t1 > trigger_px:
        rr_at_trigger = round((t1 - trigger_px) / (trigger_px - stop), 4)

    exit_reason = None
    net_r = None
    if filled and terminal:
        exit_reason = "STOP" if hit_stop else ("T2" if hit_t2 else ("T1" if hit_t1 else "TIME_EXIT"))
        net_r = _net_r(fill_px, exit_px, stop)
    elif filled:
        exit_reason = "OPEN"  # unrealized inside the walk: no R claimed

    return {
        "ticker": ep["ticker"],
        "setup_date": setup_date,
        "rule": rule,
        "source": ep.get("source"),
        "last_verdict": ep.get("last_verdict"),
        "trigger_px": round(_f(sig.get("trigger_px")), 4),
        "rr_at_trigger": rr_at_trigger,
        "rvol_on_break": sig.get("rvol_on_break"),
        "filled": filled,
        "fill_price": res.get("fill_price"),
        "exit_price": res.get("exit_price"),
        "exit_reason": exit_reason,
        "r_net": net_r,
        "bars_held": res.get("bars_held"),
        "bars_in_walk": max(0, len([b for b in series if b["date"] > setup_date][: MAX_HOLDING_BARS])),
    }


def _bucket_stats(rs: List[float], n_bars: int) -> Dict[str, Any]:
    if not rs:
        return {"n": 0, "win_rate": 0.0, "expectancy_r": 0.0, "median_r": None}
    wins = sum(1 for v in rs if v > 0)
    return {
        "n": len(rs),
        "win_rate": round(wins / len(rs), 4),
        "expectancy_r": round(sum(rs) / len(rs), 4),
        "median_r": round(sorted(rs)[len(rs) // 2] if len(rs) % 2 else (sorted(rs)[len(rs) // 2 - 1] + sorted(rs)[len(rs) // 2]) / 2, 4),
        "bars": n_bars,
        "fire_rate": round(len(rs) / n_bars, 5) if n_bars else None,
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-rule rollup: era split, halves, ticker breadth, median R, fire-rate, verdict."""
    per_rule: Dict[str, Any] = {}
    for rule in RULES:
        rule_rows = [r for r in rows if r["rule"] == rule]
        resolved = [r for r in rule_rows if r.get("r_net") is not None]
        rs = [float(r["r_net"]) for r in resolved]
        bars_total = sum(int(r.get("bars_in_walk") or 0) for r in rule_rows)

        eras = {
            "2006-2015": _bucket_stats([float(r["r_net"]) for r in resolved if str(r["setup_date"])[:4] < ERA_SPLIT_YEAR],
                                       sum(int(r.get("bars_in_walk") or 0) for r in rule_rows if str(r["setup_date"])[:4] < ERA_SPLIT_YEAR)),
            "2016-2026": _bucket_stats([float(r["r_net"]) for r in resolved if str(r["setup_date"])[:4] >= ERA_SPLIT_YEAR],
                                       sum(int(r.get("bars_in_walk") or 0) for r in rule_rows if str(r["setup_date"])[:4] >= ERA_SPLIT_YEAR)),
        }
        ordered = sorted(rule_rows, key=lambda r: r["setup_date"])
        half = max(1, len(ordered) // 2)
        halves = {
            "first_half": _bucket_stats([float(r["r_net"]) for r in resolved if r in ordered[:half]],
                                        sum(int(r.get("bars_in_walk") or 0) for r in ordered[:half])),
            "second_half": _bucket_stats([float(r["r_net"]) for r in resolved if r in ordered[half:]],
                                         sum(int(r.get("bars_in_walk") or 0) for r in ordered[half:])),
        }
        overall = _bucket_stats(rs, bars_total)
        fire_rate = overall.get("fire_rate") or 0.0
        breadth = sorted({r["ticker"] for r in resolved})
        passes = (
            overall["n"] >= MIN_REPLAYS
            and overall["win_rate"] >= MIN_WIN_RATE
            and overall["expectancy_r"] > MIN_EXPECTANCY
            and fire_rate <= MAX_FIRE_RATE
        )
        per_rule[rule] = {
            "replays": len(rule_rows),
            "fills": sum(1 for r in rule_rows if r.get("filled")),
            "resolved": overall["n"],
            "win_rate": overall["win_rate"],
            "expectancy_r": overall["expectancy_r"],
            "median_r": overall["median_r"],
            "fire_rate": fire_rate,
            "fire_rate_max": MAX_FIRE_RATE,
            "ticker_breadth": len(breadth),
            "tickers": breadth,
            "era_split": eras,
            "halves": halves,
            "min_replays": MIN_REPLAYS,
            "min_win_rate": MIN_WIN_RATE,
            "rr_floor": min_rr(),
            "verdict": "MEASURED" if passes else "UNMEASURED",
            "actionable_wording_allowed": passes,
            "note": (
                f"{overall['n']} resolved fills of {len(rule_rows)} replays across {len(breadth)} tickers; "
                f"{overall['win_rate']:.0%} wins; median {overall['median_r']}R; expectancy {overall['expectancy_r']:+.2f}R; "
                f"fire-rate {fire_rate:.2%} of {bars_total} bars (max {MAX_FIRE_RATE:.0%}). "
                f"Needs >={MIN_REPLAYS} resolved fills, >{MIN_WIN_RATE:.0%} win rate, positive expectancy, "
                f"and fire-rate under {MAX_FIRE_RATE:.0%} to claim actionable."
            ),
        }
    return per_rule


def fixture_outcomes(fixtures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each named fixture's alert outcome at its cross time (plan verification).

    A fixture either produces an alert at the cross (trigger price + stated R:R at
    market) or is documented as a no-trigger with the reason — never silently absent
    and never replayed against invented levels.
    """
    out: List[Dict[str, Any]] = []
    for fx in fixtures:
        row: Dict[str, Any] = {
            "ticker": fx["ticker"],
            "dossier_date": fx["dossier_date"],
            "dossier_spot": fx["dossier_spot"],
            "live_spot": fx.get("live_spot"),
            "replayable": fx["replayable"],
            "plan_date": (fx.get("plan") or {}).get("setup_date"),
        }
        if not fx["replayable"]:
            row["alert_outcome"] = "NO_STORED_PLAN"
            row["reason"] = fx.get("no_stored_plan_reason")
            out.append(row)
            continue

        plan = dict(fx["plan"])
        plan["setup_date"] = fx["dossier_date"]
        ep = {"ticker": fx["ticker"], "setup_date": fx["dossier_date"], "source": "fixture", **plan}
        bars = _bars_frame(fx["ticker"], (datetime.strptime(fx["dossier_date"], "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d"))
        series = _row_series(bars) if bars is not None else []
        stop = _f(plan.get("tactical_stop"))
        if not series:
            row["alert_outcome"] = "NO_BARS"
            row["reason"] = "no bars available for the fixture window"
            out.append(row)
            continue
        triggers = []
        for rule in RULES:
            sig = rule_signal(rule, ep, series) if series else None
            if sig:
                trig_px = _f(sig.get("trigger_px"))
                sig_stop = _f(sig.get("stop_loss"))
                sig_t1 = _f(sig.get("target_1"))
                rr = None
                if trig_px > sig_stop and sig_t1 > trig_px:
                    rr = round((sig_t1 - trig_px) / (trig_px - sig_stop), 4)
                triggers.append({
                    "rule": rule,
                    "trigger_px": round(trig_px, 4),
                    "rr_at_market": rr,
                    "stop": sig_stop,
                    "target": sig_t1,
                    "rvol_on_break": sig.get("rvol_on_break"),
                })
        if triggers:
            row["alert_outcome"] = "ALERT_AT_CROSS"
            row["triggers"] = triggers
        else:
            t1 = _f(plan.get("target_1"))
            entry_high = _f(plan.get("entry_zone_high"))
            reason = "no rule triggered inside the fixture's walk"
            if t1 > 0 and entry_high > 0 and t1 <= entry_high:
                reason = (
                    f"stored plan has no room at its own zone top (target_1 ${t1:.2f} <= "
                    f"entry_zone_high ${entry_high:.2f}) — the cross is already at/past T1"
                )
            elif not series:
                reason = "no bars available for the fixture window"
            row["alert_outcome"] = "NO_TRIGGER"
            row["reason"] = reason
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the runaway / breakout rules over the missed-move corpus.")
    parser.add_argument("--end-date", type=str, default="2026-08-21", help="in-sample corpus end date (default 2026-08-21)")
    parser.add_argument("--max-episodes", type=int, default=200)
    parser.add_argument("--out", type=str, default=None, help="corpus json path (default: data/research/replay_runaway_rules_<date>.json)")
    parser.add_argument("--write-labels", action="store_true", help="also write data/research/rule_labels.json for the alerts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    fixtures = build_fixtures()
    replay_dir = config.BASE_DIR / "data" / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    fixtures_path = replay_dir / "runaway_fixtures.json"

    episodes: Dict[str, Dict[str, Any]] = {}
    for fx in fixtures:
        if fx.get("replayable") and fx.get("plan"):
            ep = dict(fx["plan"])
            ep.update({"ticker": fx["ticker"], "source": "named_fixture", "last_verdict": fx.get("last_verdict")})
            episodes[fx["ticker"]] = ep
    for ep in discover_episodes(args.end_date, max_episodes=args.max_episodes):
        episodes.setdefault(ep["ticker"], ep)

    if not episodes:
        logger.error("No replayable episodes (no stored dossiers with real plan levels).")
        return 1

    rows: List[Dict[str, Any]] = []
    for ep in episodes.values():
        for rule in RULES:
            row = replay_episode(rule, ep)
            if row:
                rows.append(row)

    summary = summarize(rows)
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = config.BASE_DIR / "data" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / f"replay_runaway_rules_{date_str}.json"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "corpus_end_date": args.end_date,
        "episodes": len(episodes),
        "episode_tickers": sorted(episodes.keys()),
        "replays": len(rows),
        "rows": rows,
        "summary": summary,
        "fixtures": fixture_outcomes(fixtures),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Fixtures land under data/replay/ as the plan requires.
    fixtures_path.write_text(json.dumps(payload["fixtures"], indent=2), encoding="utf-8")

    print(f"\n=== RUNAWAY RULE REPLAY CORPUS ({len(episodes)} episodes, {len(rows)} replays, ends {args.end_date}) ===")
    for rule, s in summary.items():
        print(f"  {rule:18s} " + " | ".join([
            f"fills={s['fills']}",
            f"resolved={s['resolved']}",
            f"win={s['win_rate']:.0%}",
            f"median={s['median_r']}R",
            f"E={s['expectancy_r']:+.2f}R",
            f"fire={s['fire_rate']:.2%}/{s['fire_rate_max']:.0%}",
            f"breadth={s['ticker_breadth']}",
            f"era05-15 n={s['era_split']['2006-2015']['n']} E={s['era_split']['2006-2015']['expectancy_r']:+.2f}R",
            f"era16-26 n={s['era_split']['2016-2026']['n']} E={s['era_split']['2016-2026']['expectancy_r']:+.2f}R",
            f"verdict={s['verdict']}",
        ]))
        print(f"    {s['note']}")
    print(f"\nCorpus: {out_path}")
    print(f"Fixtures: {fixtures_path}")
    for fx in payload["fixtures"]:
        line = f"  fixture {fx['ticker']}: {fx['alert_outcome']}"
        if fx.get("triggers"):
            line += f" — {fx['triggers']}"
        elif fx.get("reason"):
            line += f" — {fx['reason']}"
        print(line)

    if args.write_labels:
        labels_path = out_dir / "rule_labels.json"
        labels_path.write_text(
            json.dumps({"generated_at": payload["generated_at"], "rules": summary}, indent=2),
            encoding="utf-8",
        )
        print(f"Rule labels: {labels_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
