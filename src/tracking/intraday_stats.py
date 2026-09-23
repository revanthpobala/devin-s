"""
Nightly Intraday Statistics & Postmortem Learnings Generator
Aggregates intraday_signals into R performance by Grade, Hour, Score Band, and Veto Reason.
Regenerates skills/postmortem_learnings.md with empirical figures so the LLM reads
measured statistics rather than discretionary anecdotes.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config
from src.tracking import alert_db
from src.tracking.alert_db import get_eastern_now

logger = logging.getLogger(__name__)


def _sanitize_veto_reason(reason: Optional[str]) -> str:
    """Normalize veto strings into clean aggregate categories."""
    if not reason:
        return "NONE (EXECUTED)"
    r_up = reason.upper()
    if "GRADE" in r_up:
        return "GRADE VETO (NOT GRADE A)"
    if "COUNTER-STAGE" in r_up or "STAGE 4" in r_up or "STAGE 2" in r_up or "REGIME VETO" in r_up:
        return "WEINSTEIN COUNTER-STAGE"
    if "EXHAUSTION TRAP" in r_up or "10:30-11:30" in r_up:
        return "MID-MORNING EXHAUSTION (10:30-11:30 ET)"
    if "MAX_CONCURRENT" in r_up or "EXPOSURE" in r_up:
        return "MAX CONCURRENT EXPOSURE"
    if "DAY PAUSE" in r_up:
        return "DAY PAUSE CIRCUIT BREAKER"
    if "LUNCH CHOP" in r_up:
        return "LUNCH CHOP VETO"
    if "EXCLUDED" in r_up:
        return "EXCLUDED UNIVERSE"
    if "AI VETO" in r_up or "STAND ASIDE" in r_up:
        return "AI TRIAGE STAND ASIDE"
    return reason[:40].strip()


def compute_group_stats(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate n, mean R, win rate, and stop rate for a list of signal dicts."""
    n = len(records)
    if n == 0:
        return {"n": 0, "scored_n": 0, "mean_r": 0.0, "win_rate": 0.0, "stop_rate": 0.0}

    r_values = []
    wins = 0
    stops = 0
    for r in records:
        val = r.get("exit_r")
        if val is None:
            val = r.get("pine_exit_r") or r.get("replay_r")
        if val is not None:
            try:
                r_num = float(val)
                r_values.append(r_num)
                if r_num > 0:
                    wins += 1
                elif r_num <= -0.9:
                    stops += 1
            except (ValueError, TypeError):
                pass

    scored_n = len(r_values)
    mean_r = round(sum(r_values) / scored_n, 3) if scored_n > 0 else 0.0
    win_rate = round(wins / scored_n * 100.0, 1) if scored_n > 0 else 0.0
    stop_rate = round(stops / scored_n * 100.0, 1) if scored_n > 0 else 0.0

    return {
        "n": n,
        "scored_n": scored_n,
        "mean_r": mean_r,
        "win_rate": win_rate,
        "stop_rate": stop_rate,
    }


def generate_postmortem_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load all intraday_signals and generate structured multi-factor R attribution."""
    path = db_path or alert_db.DB_PATH
    if not path.exists():
        logger.warning(f"Database file not found at {path}")
        return {}

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT * FROM intraday_signals ORDER BY date ASC, entry_ts ASC")
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not rows:
        return {}

    by_grade: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_hour: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    by_score: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_veto: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_day_grade_a: Dict[str, List[float]] = defaultdict(list)

    grade_a_scored_pairs = []

    for row in rows:
        grade = str(row.get("grade") or "UNKNOWN").upper()
        by_grade[grade].append(row)

        hour = row.get("hour")
        if hour is not None:
            by_hour[int(hour)].append(row)

        score = float(row.get("score") or 0.0)
        if score >= 90:
            score_band = "90+"
        elif score >= 85:
            score_band = "85-89"
        elif score >= 80:
            score_band = "80-84"
        else:
            score_band = "< 80"
        by_score[score_band].append(row)

        veto_cat = _sanitize_veto_reason(row.get("veto_reason"))
        by_veto[veto_cat].append(row)

        # Track Grade-A Go/No-Go condition
        if grade == "A":
            r_val = row.get("exit_r") if row.get("exit_r") is not None else row.get("pine_exit_r")
            if r_val is not None:
                try:
                    r_f = float(r_val)
                    grade_a_scored_pairs.append(r_f)
                    d_str = row.get("date") or "UNKNOWN"
                    by_day_grade_a[d_str].append(r_f)
                except (ValueError, TypeError):
                    pass

    # Go / No-Go calculation
    n_a = len(grade_a_scored_pairs)
    mean_a = round(sum(grade_a_scored_pairs) / n_a, 3) if n_a > 0 else 0.0
    positive_days = sum(1 for d, vals in by_day_grade_a.items() if sum(vals) > 0)
    total_days = len(by_day_grade_a)
    day_win_rate = round(positive_days / total_days * 100.0, 1) if total_days > 0 else 0.0

    go_status = "PENDING_SAMPLE"
    if n_a >= 200:
        if mean_a >= 0.10 and day_win_rate >= 50.0:
            go_status = "GO (KEEP PUSHES)"
        else:
            go_status = "NO-GO (CONTEXT ONLY)"
    else:
        go_status = f"INSUFFICIENT SAMPLE ({n_a}/200 pairs)"

    return {
        "total_count": len(rows),
        "by_grade": {g: compute_group_stats(rows_g) for g, rows_g in by_grade.items()},
        "by_hour": {h: compute_group_stats(rows_h) for h, rows_h in sorted(by_hour.items())},
        "by_score": {s: compute_group_stats(rows_s) for s, rows_s in by_score.items()},
        "by_veto": {v: compute_group_stats(rows_v) for v, rows_v in sorted(by_veto.items())},
        "go_no_go": {
            "n_grade_a": n_a,
            "mean_grade_a_r": mean_a,
            "positive_days": positive_days,
            "total_days": total_days,
            "day_win_rate": day_win_rate,
            "status": go_status,
        },
    }


def regenerate_postmortem_markdown(
    output_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Run aggregation and write skills/postmortem_live.md (and skills/postmortem_learnings.md if n_grade_a >= 200)."""
    stats = generate_postmortem_stats(db_path=db_path)
    if not stats:
        stats = {
            "total_count": 0,
            "by_grade": {},
            "by_hour": {},
            "by_score": {},
            "by_veto": {},
            "go_no_go": {
                "n_grade_a": 0,
                "mean_grade_a_r": 0.0,
                "positive_days": 0,
                "total_days": 0,
                "day_win_rate": 0.0,
                "status": "INSUFFICIENT SAMPLE (0/200 pairs)",
            },
        }

    out_file = output_path or (config.BASE_DIR / "skills" / "postmortem_live.md")
    now_str = get_eastern_now().strftime("%Y-%m-%d %H:%M ET")

    lines = [
        "# Active Session Post-Mortem Learnings & Empirical Edge",
        "",
        f"> 📊 **Empirical Performance Audit** — Generated automatically at `{now_str}` from `{stats['total_count']}` total signals in `intraday_signals`.",
        "",
        "---",
        "",
        "## 1. Go / No-Go Decision Gate (n ≥ 200 Pairs)",
        "",
        "| Metric | Target | Current Measured Value | Status |",
        "|---|---|---|---|",
        f"| **Grade-A Scored Pairs (n)** | `≥ 200` | **`{stats['go_no_go']['n_grade_a']}`** | `{'PASS' if stats['go_no_go']['n_grade_a'] >= 200 else 'ACCUMULATING'}` |",
        f"| **Grade-A Mean Expectancy (R)** | `≥ +0.10 R` | **`{stats['go_no_go']['mean_grade_a_r']:+.3f} R`** | `{'PASS' if stats['go_no_go']['mean_grade_a_r'] >= 0.10 else 'MONITOR'}` |",
        f"| **Day Win Rate (% Positive Days)** | `> 50.0%` | **`{stats['go_no_go']['day_win_rate']}%`** ({stats['go_no_go']['positive_days']}/{stats['go_no_go']['total_days']} days) | `{'PASS' if stats['go_no_go']['day_win_rate'] >= 50.0 else 'MONITOR'}` |",
        f"| **Operating Directive** | Active Push Status | **`{stats['go_no_go']['status']}`** | — |",
        "",
        "> [!IMPORTANT]",
        "> At n ≥ 200 grade-A pairs with `trade_id`, keep live desktop/push alerts ONLY if mean exit R is ≥ +0.10 and positive on the majority of trading days. Otherwise, the weekly indicator script is treated as context only. Do NOT tune time gates or discretionary vetoes before hitting the 200-pair threshold.",
        "",
        "---",
        "",
        "## 2. R Attribution by Setup Grade",
        "",
        "| Grade | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Net Expectancy |",
        "|---|---|---|---|---|---|---|",
    ]

    for grade in sorted(stats["by_grade"].keys()):
        s = stats["by_grade"][grade]
        badge = "🟢 EDGE" if s["mean_r"] > 0 else "🔴 BLEED"
        lines.append(f"| **Grade {grade}** | {s['n']} | {s['scored_n']} | `{s['mean_r']:+.3f} R` | {s['win_rate']}% | {s['stop_rate']}% | {badge} |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. R Attribution by Hour of Day (ET)",
        "",
        "| Hour (ET) | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % | Assessment |",
        "|---|---|---|---|---|---|---|",
    ])

    for hour, s in stats["by_hour"].items():
        label = f"{hour:02d}:00 – {hour:02d}:59 ET"
        assessment = "🟢 PRIME" if s["mean_r"] > 0 else ("🟡 CHOP" if s["mean_r"] > -0.15 else "⛔ AVOID")
        lines.append(f"| **{label}** | {s['n']} | {s['scored_n']} | `{s['mean_r']:+.3f} R` | {s['win_rate']}% | {s['stop_rate']}% | {assessment} |")

    lines.extend([
        "",
        "---",
        "",
        "## 4. R Attribution by Conviction Score Band",
        "",
        "| Score Band | Signals (n) | Scored (n) | Mean R | Win Rate % | Stop-Out % |",
        "|---|---|---|---|---|---|",
    ])

    for band in ["90+", "85-89", "80-84", "< 80"]:
        if band in stats["by_score"]:
            s = stats["by_score"][band]
            lines.append(f"| **{band}** | {s['n']} | {s['scored_n']} | `{s['mean_r']:+.3f} R` | {s['win_rate']}% | {s['stop_rate']}% |")

    lines.extend([
        "",
        "---",
        "",
        "## 5. Shadow Ledger Veto Attribution",
        "",
        "| Veto Reason | Signals (n) | Scored (n) | Counterfactual Mean R | Win % | Capital Impact |",
        "|---|---|---|---|---|---|",
    ])

    for veto, s in stats["by_veto"].items():
        if veto == "NONE (EXECUTED)":
            impact = "EXECUTED IN REAL-TIME"
        elif s["mean_r"] < 0:
            impact = f"🟢 SAVED CAPITAL ({s['mean_r']:+.2f}R avoided)"
        else:
            impact = f"🔴 MISSED RUNNER ({s['mean_r']:+.2f}R blocked)"
        lines.append(f"| **{veto}** | {s['n']} | {s['scored_n']} | `{s['mean_r']:+.3f} R` | {s['win_rate']}% | {impact} |")

    lines.append("")
    content = "\n".join(lines)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(content, encoding="utf-8")
    logger.info(f"Successfully generated {out_file} with empirical live stats.")

    # Only replace postmortem_learnings.md once there are at least 200 scored grade-A rows
    if output_path is None:
        learnings_file = config.BASE_DIR / "skills" / "postmortem_learnings.md"
        if stats.get("go_no_go", {}).get("n_grade_a", 0) >= 200:
            learnings_file.write_text(content, encoding="utf-8")
            logger.info(f"Threshold reached (n>=200)! Replaced {learnings_file} with empirical stats.")
        else:
            logger.info(f"Live stats written to {out_file}; preserved benchmark {learnings_file} (n < 200).")

    return content


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    regenerate_postmortem_markdown()
