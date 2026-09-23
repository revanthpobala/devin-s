"""
Nightly Intraday Statistics & Postmortem Learnings Generator
Aggregates intraday_signals into R performance by Grade, Hour, Score Band, and Veto Reason.
Regenerates skills/postmortem_learnings.md with empirical figures so the LLM reads
measured statistics rather than discretionary anecdotes.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config
from src.tracking import alert_db
from src.tracking.alert_db import get_eastern_now

logger = logging.getLogger(__name__)

_BUCKET_CACHE: Dict[str, Any] = {"timestamp": 0.0, "db_path": "", "rows": []}


def clear_bucket_stats_cache() -> None:
    """Clear in-memory 5-minute bucket stats cache."""
    global _BUCKET_CACHE
    _BUCKET_CACHE = {"timestamp": 0.0, "db_path": "", "rows": []}


def _get_cached_scored_rows(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    global _BUCKET_CACHE
    path = db_path or alert_db.DB_PATH
    now = time.time()
    if (
        _BUCKET_CACHE["rows"]
        and _BUCKET_CACHE["db_path"] == str(path)
        and (now - _BUCKET_CACHE["timestamp"]) < 300.0
    ):
        return _BUCKET_CACHE["rows"]

    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM intraday_signals
            WHERE COALESCE(pine_exit_r, exit_r) IS NOT NULL
            ORDER BY date ASC, entry_ts ASC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    _BUCKET_CACHE = {"timestamp": now, "db_path": str(path), "rows": rows}
    return rows


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
        val = r.get("pine_exit_r")
        if val is None:
            val = r.get("exit_r") or r.get("replay_r")
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


def lookup_bucket_stats(
    grade: Optional[str],
    hour: Optional[int],
    score: Optional[float],
    min_n: int = 30,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Look up empirical edge stats for a specific (grade, hour, score) bucket.

    Rows: COALESCE(pine_exit_r, exit_r) is not null.
    Bucket: grade x hour band (9-10 / 11-13 / 14-15) x score band (<85 / >=85).
    Math: reuse compute_group_stats.
    Output: a stub when n < min_n; cache for 5 minutes.
    """
    grade_missing = grade in (None, "", "NULL")
    score_missing = score in (None, "", "NULL")
    if grade_missing or score_missing or hour is None:
        parts = []
        if grade_missing:
            parts.append("Grade NULL")
        if score_missing:
            parts.append("Score NULL")
        prefix = f"{' · '.join(parts)} " if parts else ""
        return {
            "n": 0,
            "scored_n": 0,
            "mean_r": 0.0,
            "win_rate": 0.0,
            "stop_rate": 0.0,
            "read": False,
            "text": f"{prefix}n=0, no read yet".strip(),
        }

    try:
        score_num = float(score)
    except (ValueError, TypeError):
        return {
            "n": 0,
            "scored_n": 0,
            "mean_r": 0.0,
            "win_rate": 0.0,
            "stop_rate": 0.0,
            "read": False,
            "text": "Score NULL n=0, no read yet",
        }

    try:
        hour_num = int(hour)
    except (ValueError, TypeError):
        return {
            "n": 0,
            "scored_n": 0,
            "mean_r": 0.0,
            "win_rate": 0.0,
            "stop_rate": 0.0,
            "read": False,
            "text": "n=0, no read yet",
        }

    if 9 <= hour_num <= 10:
        h_band = "9-10"
        h_label = "9-10h"
    elif 11 <= hour_num <= 13:
        h_band = "11-13"
        h_label = "11-13h"
    elif 14 <= hour_num <= 15:
        h_band = "14-15"
        h_label = "14-15h"
    else:
        h_band = f"{hour_num}"
        h_label = f"{hour_num}h"

    if score_num < 85:
        s_band = "<85"
        s_label = "score<85"
    else:
        s_band = "85+"
        s_label = "score85+"

    grade_clean = str(grade).strip().upper()
    rows = _get_cached_scored_rows(db_path=db_path)

    matched_rows = []
    for r in rows:
        r_grade = str(r.get("grade") or "").strip().upper()
        if r_grade != grade_clean:
            continue

        r_hour = r.get("hour")
        if r_hour is None:
            ts = str(r.get("entry_ts") or "")
            if len(ts) >= 2 and ts[:2].isdigit():
                try:
                    r_hour = int(ts[:2])
                except ValueError:
                    r_hour = None
        if r_hour is None:
            continue

        if 9 <= int(r_hour) <= 10:
            r_h_band = "9-10"
        elif 11 <= int(r_hour) <= 13:
            r_h_band = "11-13"
        elif 14 <= int(r_hour) <= 15:
            r_h_band = "14-15"
        else:
            r_h_band = f"{r_hour}"

        if r_h_band != h_band:
            continue

        r_score = r.get("score")
        if r_score is None:
            continue
        try:
            r_score_num = float(r_score)
        except (ValueError, TypeError):
            continue

        r_s_band = "<85" if r_score_num < 85 else "85+"
        if r_s_band != s_band:
            continue

        matched_rows.append(r)

    stats = compute_group_stats(matched_rows)
    n = stats["scored_n"]
    if n < min_n:
        stats["read"] = False
        stats["text"] = f"n={n}, no read yet"
        return stats

    stats["read"] = True
    stats["text"] = (
        f"Grade {grade_clean} {h_label} {s_label} {stats['mean_r']:+.2f}R "
        f"n={n} win {int(round(stats['win_rate']))}%"
    )
    return stats


def generate_postmortem_stats(
    db_path: Optional[Path] = None,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    """Load all intraday_signals and generate structured multi-factor R attribution."""
    path = db_path or alert_db.DB_PATH
    if not path.exists():
        logger.warning(f"Database file not found at {path}")
        return {}

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        if since:
            cur.execute(
                "SELECT * FROM intraday_signals WHERE date >= ? ORDER BY date ASC, entry_ts ASC",
                (since,),
            )
        else:
            cur.execute("SELECT * FROM intraday_signals ORDER BY date ASC, entry_ts ASC")
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not rows:
        return {
            "total_count": 0,
            "n_scored": 0,
            "by_grade": {},
            "by_hour": {},
            "by_score": {},
            "by_veto": {},
            "by_llm": {
                "TAKE": {"n": 0, "scored_n": 0, "mean_r": 0.0, "win_rate": 0.0, "stop_rate": 0.0, "read": False},
                "VETO": {"n": 0, "scored_n": 0, "mean_r": 0.0, "win_rate": 0.0, "stop_rate": 0.0, "read": False},
                "GATE": {"n": 0, "scored_n": 0, "mean_r": 0.0, "win_rate": 0.0, "stop_rate": 0.0, "read": False},
            },
            "go_no_go": {
                "n_grade_a": 0,
                "mean_grade_a_r": 0.0,
                "positive_days": 0,
                "total_days": 0,
                "day_win_rate": 0.0,
                "status": "INSUFFICIENT SAMPLE (0/200 pairs)",
            },
        }

    by_grade: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_hour: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    by_score: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_veto: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_llm: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
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

        v_raw = str(row.get("llm_verdict") or "").strip().upper()
        if v_raw.startswith("TAKE"):
            llm_cat = "TAKE"
        elif v_raw.startswith("VETO"):
            llm_cat = "VETO"
        elif v_raw.startswith("GATE"):
            llm_cat = "GATE"
        else:
            if row.get("taken"):
                llm_cat = "TAKE"
            elif row.get("veto_reason"):
                v_hdr = str(row.get("veto_reason")).upper()
                if "GRADE" in v_hdr or "STAND ASIDE (" in v_hdr:
                    llm_cat = "GATE"
                else:
                    llm_cat = "VETO"
            else:
                llm_cat = "TAKE"
        by_llm[llm_cat].append(row)

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

    def _with_read_flag(st: Dict[str, Any]) -> Dict[str, Any]:
        st["read"] = st.get("scored_n", st.get("n", 0)) >= 30
        return st

    n_scored = sum(
        1 for r in rows
        if r.get("exit_r") is not None or r.get("pine_exit_r") is not None or r.get("replay_r") is not None
    )

    return {
        "total_count": len(rows),
        "n_scored": n_scored,
        "by_grade": {g: _with_read_flag(compute_group_stats(rows_g)) for g, rows_g in by_grade.items()},
        "by_hour": {h: _with_read_flag(compute_group_stats(rows_h)) for h, rows_h in sorted(by_hour.items())},
        "by_score": {s: _with_read_flag(compute_group_stats(rows_s)) for s, rows_s in by_score.items()},
        "by_veto": {v: _with_read_flag(compute_group_stats(rows_v)) for v, rows_v in sorted(by_veto.items())},
        "by_llm": {
            k: _with_read_flag(compute_group_stats(by_llm.get(k, [])))
            for k in ["TAKE", "VETO", "GATE"]
        },
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
