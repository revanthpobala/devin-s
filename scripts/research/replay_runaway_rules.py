#!/usr/bin/env python3
"""
scripts/research/replay_runaway_rules.py — E1 from plan/no-lost-movers.md.

Replays each missed move under each candidate runaway / breakout rule and writes the
corpus. No target selection: every episode discovered by the missed-move scan gets
replayed under every rule, so the rule cannot be shown in its best light.

Rules replayed (all against the real plan levels, honest bar math):

  zone_top_market   buy at market when price is above the zone top and below T1
                    (R:R at market >= the floor) — the "it left the zone while I was
                    stalking" trade.
  breakout_trigger  buy at the plan's breakout level.
  pullback_zone_top buy on a pullback back into entry_high.

Replay math is `evaluate_setup_lifecycle_bars` (src/tracking/execution_validator.py):
next-open entry, stop-first same-bar ambiguity, 21-bar walk. No new lifecycle engine
is written for this.

Outputs (data/research/):
  replay_runaway_rules_<date>.json   the full corpus: one row per (episode, rule)
  rule_labels.json                   per-rule verdict the alerts consult (E2)

The corpus is small by nature. A rule may only say "actionable" once it passes on at
least MIN_REPLAYS episodes with positive expectancy; below that it stays UNMEASURED
and its alerts keep the watch-only wording. Every number is printed so the claim can
never be oversized.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src import config  # noqa: E402
from src.tracking.execution_validator import evaluate_setup_lifecycle_bars, get_bars_since_date  # noqa: E402
from src.tracking.rr_config import min_rr  # noqa: E402

logger = logging.getLogger("replay_runaway_rules")

# The honest pass bar for this corpus. It is printed with every claim.
MIN_REPLAYS = 20
MIN_WIN_RATE = 0.45
MIN_EXPECTANCY = 0.0
MAX_HOLDING_BARS = 21

RULES = ("zone_top_market", "breakout_trigger", "pullback_zone_top")

# The three named moves this replayer must run (plan E1). One has an on-disk dossier
# with full plan levels; the other two carry the price pairs the audit recorded.
NAMED_FIXTURES = [
    {
        "ticker": "META",
        "dossier_date": "2026-10-09",
        "dossier_spot": 718.67,
        "verdict": "CUT",
        "last_verdict": "CUT",
        "source": "dossier",
    },
    {"ticker": "VST", "dossier_date": "2026-10-08", "dossier_spot": 740.67, "live_spot": 760.76,
     "last_verdict": "WATCH", "source": "fixture"},
    {"ticker": "NRG", "dossier_date": "2026-10-08", "dossier_spot": 385.63, "live_spot": 418.90,
     "last_verdict": "WATCH", "source": "fixture"},
]


def _latest_dossier_plan(ticker: str, dossier_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The plan levels from the ticker's most recent stored dossier (preferring the named date)."""
    safe = ticker.replace(":", "_").upper()
    base = config.BASE_DIR / "data"
    candidates: List[Path] = []
    for sub in ("raw", "triage"):
        sub_root = base / sub
        if not sub_root.exists():
            continue
        for wl in sub_root.glob(f"*/{safe}/{safe}_watch_levels.json"):
            candidates.append(wl)
        for wl in sub_root.glob(f"*/{safe}/{safe}_watch_levels.json"):
            candidates.append(wl)
    if not candidates:
        return None
    candidates.sort(key=lambda p: str(p.parent.parent.name), reverse=True)
    preferred = [p for p in candidates if dossier_date and str(p.parent.parent.name).startswith(str(dossier_date)[:10])]
    chosen = preferred[0] if preferred else candidates[0]
    try:
        data = json.loads(chosen.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"dossier parse failed for {safe}: {e}")
        return None
    sp = data.get("shares_plan") or {}
    return {
        "ticker": safe,
        "setup_date": str(chosen.parent.parent.name)[:10],
        "entry_zone_low": sp.get("entry_zone_low") or (sp.get("entry_zone") or [0, 0])[0],
        "entry_zone_high": sp.get("entry_zone_high") or (sp.get("entry_zone") or [0, 0])[1],
        "breakout_level": sp.get("breakout_level") or 0.0,
        "breakout_stop": sp.get("breakout_stop") or sp.get("tactical_stop") or 0.0,
        "tactical_stop": sp.get("tactical_stop") or 0.0,
        "target_1": sp.get("target_1") or 0.0,
        "target_2": sp.get("target_2") or 0.0,
    }


def discover_episodes(lookback_days: int = 21, max_episodes: int = 60) -> List[Dict[str, Any]]:
    """Every missed-move episode available locally: named fixtures + the dossier scan."""
    episodes: Dict[str, Dict[str, Any]] = {}

    for fx in NAMED_FIXTURES:
        plan = _latest_dossier_plan(fx["ticker"], fx["dossier_date"])
        if not plan:
            continue
        ep = dict(fx)
        ep.update(plan)
        episodes[fx["ticker"]] = ep

    # The dossier scan is generalized discovery: any ticker with a stored plan inside the
    # lookback window joins the corpus, not just the three named moves.
    base = config.BASE_DIR / "data"
    today = datetime.now().date()
    if base.exists():
        for sub in ("raw", "triage"):
            sub_root = base / sub
            if not sub_root.exists():
                continue
            for dt_dir in sub_root.iterdir():
                if not dt_dir.is_dir() or len(dt_dir.name) != 10:
                    continue
                try:
                    if (today - datetime.strptime(dt_dir.name, "%Y-%m-%d").date()).days > lookback_days:
                        continue
                except Exception:
                    continue
                for wl in dt_dir.glob("*/*_watch_levels.json"):
                    sym = wl.name.replace("_watch_levels.json", "").upper()
                    if sym in episodes:
                        continue
                    try:
                        data = json.loads(wl.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    sp = data.get("shares_plan") or {}
                    if not (sp.get("entry_zone_low") or (sp.get("entry_zone") or [0])[0]):
                        continue
                    episodes[sym] = {
                        "ticker": sym,
                        "dossier_date": dt_dir.name,
                        "last_verdict": (data.get("verdict") or "STALK"),
                        "source": "dossier_scan",
                        "setup_date": dt_dir.name,
                        "entry_zone_low": sp.get("entry_zone_low") or 0.0,
                        "entry_zone_high": sp.get("entry_zone_high") or 0.0,
                        "breakout_level": sp.get("breakout_level") or 0.0,
                        "breakout_stop": sp.get("breakout_stop") or sp.get("tactical_stop") or 0.0,
                        "tactical_stop": sp.get("tactical_stop") or 0.0,
                        "target_1": sp.get("target_1") or 0.0,
                        "target_2": sp.get("target_2") or 0.0,
                    }

    out = [e for e in episodes.values() if _f(e.get("tactical_stop")) > 0 and _f(e.get("target_1")) > 0]
    return out[:max_episodes]


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bars_market_px(bars, as_of: str) -> float:
    """The last close on or before the setup date — the print a market order would have paid."""
    try:
        idx = [str(x)[:10] for x in bars.index]
        closes = []
        for d, i in zip(idx, range(len(bars))):
            if d <= as_of:
                row = bars.iloc[i]
                c = _f(row.get("Close") if hasattr(row, "get") else None)
                if c > 0:
                    closes.append((d, c))
        return closes[-1][1] if closes else 0.0
    except Exception:
        return 0.0


def rule_kwargs(rule: str, plan: Dict[str, Any], floor: float, market_px: float) -> Optional[Dict[str, Any]]:
    """The lifecycle kwargs for one rule on one plan. None when the plan cannot express it."""
    entry_high = _f(plan.get("entry_zone_high"))
    stop = _f(plan.get("tactical_stop")) or _f(plan.get("breakout_stop"))
    t1 = _f(plan.get("target_1"))
    t2 = _f(plan.get("target_2"))
    if stop <= 0 or t1 <= 0:
        return None

    if rule == "zone_top_market":
        # Market entry only when R:R at the zone-top market print clears the floor.
        if entry_high <= 0:
            return None
        px = market_px
        if px <= entry_high or (t1 > 0 and px >= t1):
            return None
        if (t1 - px) / (px - stop) < floor:
            return None
        return dict(entry_type="BREAKOUT", breakout_level=entry_high, stop_loss=stop,
                   target_1=t1, target_2=t2)
    if rule == "breakout_trigger":
        bl = _f(plan.get("breakout_level"))
        if bl <= 0:
            return None
        return dict(entry_type="BREAKOUT", breakout_level=bl, stop_loss=stop,
                   target_1=t1, target_2=t2)
    if rule == "pullback_zone_top":
        if entry_high <= 0:
            return None
        return dict(entry_type="LIMIT", entry_low=round(entry_high * 0.99, 4),
                   entry_high=round(entry_high * 1.005, 4), stop_loss=stop,
                   target_1=t1, target_2=t2)
    return None


def replay_rule(rule: str, ep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Replay one rule on one episode. Returns the episode row, or None if unrepresentable."""
    bars = get_bars_since_date(ep["ticker"], (datetime.strptime(ep["setup_date"], "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d"))
    if bars is None or len(bars) < 2:
        return None

    market_px = _bars_market_px(bars, ep["setup_date"])
    kw = rule_kwargs(rule, ep, min_rr(), market_px)
    if kw is None:
        return None

    try:
        res = evaluate_setup_lifecycle_bars(
            bars=bars,
            setup_date=ep["setup_date"],
            side="LONG",
            live_price=market_px or _f(ep.get("dossier_spot")),
            current_status="STALKING",
            max_holding_bars=MAX_HOLDING_BARS,
            **kw,
        )
    except Exception as e:
        logger.debug(f"[{ep['ticker']}] {rule} replay failed: {e}")
        return None

    filled = bool(res.get("was_filled"))
    fill_px = _f(res.get("fill_price"))
    stop = _f(kw.get("stop_loss"))
    hit_t1 = bool(res.get("hit_t1"))
    hit_t2 = bool(res.get("hit_t2"))
    hit_stop = bool(res.get("hit_stop"))
    terminal = bool(res.get("is_terminal"))

    exit_reason = None
    r_multiple = None
    if filled and terminal:
        exit_px = _f(res.get("exit_price"))
        if hit_stop:
            exit_reason = "STOP"
        elif hit_t2:
            exit_reason = "T2"
        elif hit_t1:
            exit_reason = "T1"
        elif exit_px > 0:
            exit_reason = "TIME_EXIT"
        if exit_px > 0 and fill_px > 0 and stop > 0 and fill_px > stop:
            r_multiple = round((exit_px - fill_px) / (fill_px - stop), 4)
    elif filled:
        exit_reason = "OPEN"
        r_multiple = None  # unrealized inside the walk: no R claimed

    return {
        "ticker": ep["ticker"],
        "setup_date": ep["setup_date"],
        "source": ep.get("source"),
        "last_verdict": ep.get("last_verdict"),
        "rule": rule,
        "market_px": round(market_px, 4),
        "filled": filled,
        "fill_price": res.get("fill_price"),
        "exit_price": res.get("exit_price"),
        "exit_reason": exit_reason,
        "r_multiple": r_multiple,
        "hit_t1": hit_t1,
        "hit_t2": hit_t2,
        "hit_stop": hit_stop,
        "bars_held": res.get("bars_held"),
        "episode_index": res.get("episode_index", 0),
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-rule rollup with the honest pass bar. Prints every number alongside the verdict."""
    floor = min_rr()
    per_rule: Dict[str, Any] = {}
    for rule in RULES:
        rule_rows = [r for r in rows if r["rule"] == rule]
        filled = [r for r in rule_rows if r.get("filled")]
        resolved = [r for r in filled if r.get("r_multiple") is not None]
        rs = [float(r["r_multiple"]) for r in resolved]
        wins = sum(1 for v in rs if v > 0)
        n = len(rs)
        win_rate = (wins / n) if n else 0.0
        expectancy = (sum(rs) / n) if n else 0.0
        passes = n >= MIN_REPLAYS and win_rate >= MIN_WIN_RATE and expectancy > MIN_EXPECTANCY
        per_rule[rule] = {
            "replays": len(rule_rows),
            "fills": len(filled),
            "resolved": n,
            "win_rate": round(win_rate, 4),
            "expectancy_r": round(expectancy, 4),
            "min_replays": MIN_REPLAYS,
            "min_win_rate": MIN_WIN_RATE,
            "rr_floor": floor,
            "verdict": "MEASURED" if passes else "UNMEASURED",
            "actionable_wording_allowed": passes,
            "note": (
                f"{len(filled)} fills of {len(rule_rows)} replays, {n} resolved to an exit; "
                f"{win_rate:.0%} wins; {expectancy:+.2f}R expectancy. Needs {MIN_REPLAYS} resolved "
                f"fills with >{MIN_WIN_RATE:.0%} win rate and positive expectancy to claim actionable."
            ),
        }
    return per_rule


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay runaway / breakout rules over the missed-move corpus.")
    parser.add_argument("--lookback-days", type=int, default=21)
    parser.add_argument("--max-episodes", type=int, default=60)
    parser.add_argument("--out", type=str, default=None, help="corpus json path (default: data/research/replay_runaway_rules_<date>.json)")
    parser.add_argument("--write-labels", action="store_true", help="also write data/research/rule_labels.json for the alerts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    episodes = discover_episodes(args.lookback_days, args.max_episodes)
    if not episodes:
        logger.error("No episodes discovered (no stored dossiers with real levels).")
        return 1

    rows: List[Dict[str, Any]] = []
    for ep in episodes:
        for rule in RULES:
            row = replay_rule(rule, ep)
            if row:
                rows.append(row)

    summary = summarize(rows)
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = config.BASE_DIR / "data" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / f"replay_runaway_rules_{date_str}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "episodes": len(episodes),
        "episode_tickers": sorted({ep["ticker"] for ep in episodes}),
        "rows": rows,
        "summary": summary,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\n=== RUNWAY RULE REPLAY CORPUS ({len(episodes)} episodes, {len(rows)} replays) ===")
    for rule, s in summary.items():
        print(
            f"  {rule:18s} fills={s['fills']:<4d} win={s['win_rate']:.0%} "
            f"E={s['expectancy_r']:+.2f}R  verdict={s['verdict']}  ({s['note']})"
        )
    print(f"\nCorpus written to: {out_path}")

    if args.write_labels:
        labels_path = out_dir / "rule_labels.json"
        labels_path.write_text(
            json.dumps({"generated_at": payload["generated_at"], "rules": summary}, indent=2),
            encoding="utf-8",
        )
        print(f"Rule labels written to: {labels_path}")

    # Backfill fixtures that could not be replayed (no on-disk plan) are recorded, not dropped.
    missing = [fx["ticker"] for fx in NAMED_FIXTURES if fx["ticker"] not in {ep["ticker"] for ep in episodes}]
    if missing:
        print(f"  (named fixtures without a stored plan, skipped: {', '.join(missing)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
