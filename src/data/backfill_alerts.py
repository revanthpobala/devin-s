"""
Persist-only backfill of historical TradingView alerts.

WHY THIS EXISTS
The Gmail poller used to keep only the newest 60 messages from the sender plus UNSEEN. With 7,698
sender messages in the mailbox and none UNSEEN, 7,638 were permanently unreachable -- including the
62 intraday alerts on 2026-09-04 (the first day of the local data boundary) and the 12 Daily-Screen
alerts on 09-11. That ceiling is gone.

The remaining backlog is older than the local data boundary (2026-07-08 onward) and was never
ingested, so it never ran through the 0DTE gem either.

WHY PERSIST-ONLY
main.py's live path enriches with LIVE data: get_current_price() and get_ticker_news() at
process_alert_enrichment (main.py:392,403). Running that over a July alert would fabricate a
verdict that never happened -- today's price against a three-month-old setup, graded A/B/C, written
back as if contemporaneous. Those rows then feed intraday_signals and the intraday scoreboard.

So this records the row and stops: raw_payload preserved, llm_decision/grade/score left NULL,
routing_stage ARCHIVED, no gem call, no PositionManager, no Gmail mutation.

WHAT IT DELIBERATELY DOES NOT DO
- Does not touch alerts dated today or later. Those belong to the live poller, and claiming them
  here would skip their gem run entirely. This is the single most dangerous mistake available here,
  so it is guarded twice (default cutoff, and a hard assertion per row).
- Does not mark anything read in Gmail. Email-id dedupe is enough for the poller to skip them, and
  leaving the mailbox untouched means this is reversible.
- Does not run the LLM, so it costs nothing and cannot hallucinate.

Usage
    python run_alerts.py --backfill --dry-run      # report what would be persisted
    python run_alerts.py --backfill                # persist everything older than today
    python run_alerts.py --backfill --max-cycles 3 # partial; re-run to resume
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("backfill_alerts")

# Fields that hold an LLM verdict. Stripped before insert so a persisted row can never claim a
# grade it was never given.
_VERDICT_FIELDS = (
    "llm_decision", "llm_playbook", "grade", "score", "align", "wrong_if",
    "act_now", "verdict",
)

ARCHIVED = "ARCHIVED"


def _alert_date(alert: Dict[str, Any]) -> str:
    """Eastern date the alert belongs to, as recorded by the poller."""
    ts = alert.get("timestamp") or ""
    return ts[:10] if len(ts) >= 10 else ""


def strip_verdict_fields(alert: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with every LLM-derived field cleared."""
    clean = dict(alert)
    for field in _VERDICT_FIELDS:
        clean.pop(field, None)
    return clean


def select_backfill_batch(
    alerts: List[Dict[str, Any]],
    cutoff: str,
    today: str,
) -> tuple:
    """Split a parsed batch into (to_persist, left_to_live, unparseable).

    `cutoff` is EXCLUSIVE and ISO dates compare correctly as strings, so a row is persisted only
    when its date is strictly before it. Anything not older than the cutoff -- today's alerts, and
    anything between the cutoff and today -- is LEFT TO THE LIVE PATH. Claiming one would skip its
    0DTE gem run, so it is reported rather than written; dropping it silently would be worse still.
    """
    keep, left_to_live, bad = [], [], []
    for a in alerts:
        day = _alert_date(a)
        if not day:
            bad.append(a)
        elif day < cutoff:
            keep.append(a)
        else:
            left_to_live.append(a)
    return keep, left_to_live, bad


def run_backfill(
    limit: int = 250,
    max_cycles: Optional[int] = None,
    before: Optional[str] = None,
    dry_run: bool = False,
    gmail_client=None,
) -> Dict[str, Any]:
    """Persist historical alerts. Idempotent and resumable via alerts.email_id."""
    from src.clients.gmail_client import GmailClient
    from src.tracking.alert_db import _get_connection, get_eastern_date_str, record_alert

    own_client = gmail_client is None
    gmail = gmail_client or GmailClient()
    if own_client and not gmail.connect():
        return {"error": "could not connect to Gmail"}

    today = get_eastern_date_str()
    cutoff = before or today          # exclusive upper bound

    report: Dict[str, Any] = {
        "cutoff_exclusive": cutoff,
        "today": today,
        "persisted": 0,
        "duplicates": 0,
        "skipped_today": 0,
        "unparseable": 0,
        "by_strategy": Counter(),
        "by_date": Counter(),
        "cycles": 0,
        "dry_run": dry_run,
        "_mine": [],
    }

    try:
        while True:
            report["cycles"] += 1
            if max_cycles and report["cycles"] > max_cycles:
                logger.info("stopping at the %d-cycle cap; re-run to resume", max_cycles)
                break

            batch = gmail.fetch_new_alerts(limit=limit)
            if not batch:
                logger.info("backlog fully drained")
                break

            keep, left_to_live, bad = select_backfill_batch(batch, cutoff, today)
            report["skipped_today"] += len(left_to_live)
            report["unparseable"] += len(bad)

            if dry_run:
                report["persisted"] += len(keep)
                for a in keep:
                    report["by_strategy"][a.get("strategy", "?")] += 1
                    report["by_date"][_alert_date(a)] += 1
                continue

            for a in keep:
                day = _alert_date(a)
                # Second guard: never write a row dated today, whatever the caller passed.
                if day >= today:
                    report["skipped_today"] += 1
                    continue
                try:
                    inserted = record_alert(strip_verdict_fields(a))
                except Exception as e:
                    logger.error("persist failed for %s: %s", a.get("symbol"), e)
                    continue
                if inserted:
                    report["persisted"] += 1
                    report["by_strategy"][a.get("strategy", "?")] += 1
                    report["by_date"][day] += 1
                    # Stamp ONLY the rows this run inserted, by message_id.
                    #
                    # A blanket "UPDATE ... WHERE routing_stage='RECORDED' AND status='INGESTED'
                    # is not safe while an ingester is live: it stamps rows the ingester owns, and
                    # if that ingester's enrichment worker then finishes, the row ends up
                    # ARCHIVED *with* an llm_decision -- a verdict computed against today's tape for
                    # a September alert. Observed exactly that on 7 rows.
                    if a.get("message_id"):
                        report["_mine"].append(a["message_id"])
                else:
                    report["duplicates"] += 1

            if report["_mine"]:
                with _get_connection() as conn:
                    conn.executemany(
                        "UPDATE alerts SET routing_stage = ? WHERE message_id = ? "
                        "AND COALESCE(routing_stage, 'RECORDED') = 'RECORDED' AND status = 'INGESTED'",
                        [(ARCHIVED, mid) for mid in report["_mine"]],
                    )
                    conn.commit()
                report["_mine"] = []

            logger.info(
                "cycle %d: fetched %d, persisted %d (total %d), skipped_today %d",
                report["cycles"], len(batch), len(keep), report["persisted"],
                report["skipped_today"],
            )
            if dry_run:
                continue
    finally:
        if own_client:
            try:
                gmail.disconnect()
            except Exception:
                pass

    report["by_strategy"] = dict(report["by_strategy"])
    report["by_date"] = dict(sorted(report["by_date"].items()))
    report.pop("_mine", None)
    return report