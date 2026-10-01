"""
Alert sweep entrypoint: ENTRY + RISK + OPS at the close, OPS + digest on a timer.

    python run_alerts.py --date 2026-09-30          # one after-close pass
    python run_alerts.py --loop --interval 300      # OPS only, on a timer
    python run_alerts.py --ops-only                 # just the machine-health tier
    python run_alerts.py --dry-run                  # compute everything, print, send nothing

Scheduling: the after-close pass is the interesting one and should be run once, at or after
16:30 MT (see run_market_orchestrator). OPS is worth running all day because it is the tier that
reports a broken machine.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime

from src.tracking.entry_risk_alerts import run_daily_alert_sweep
from src.tracking.income_digest import run_digest
from src.tracking.notify import is_digest_time, purge_dedupe, sent_history
from src.tracking.ops_alerts import purge_test_rows, run_ops_checks

logger = logging.getLogger("run_alerts")


def _log_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def run_ops(args) -> int:
    report = run_ops_checks(notify=not args.dry_run)
    _log_json(report)
    return 0 if report.get("ok") else 1


def run_close_pass(args) -> int:
    date = args.date or datetime.now().strftime("%Y-%m-%d")

    if args.dry_run:
        # Compute the verdicts WITHOUT notifying. fire_entry/fire_risk dispatch for real, so a
        # dry run that called them would page the phone -- the one thing a dry run must never do.
        from src.tracking.entry_risk_alerts import evaluate_risk, load_onsets_for_date
        onsets = load_onsets_for_date(date)

        pushes, digest_candidates = [], []
        for o in onsets:
            qualifies, fails = o.entry_gate()
            if qualifies:
                pushes.append(o.ticker)
            elif o.packs_present and o.in_zone and o.rr_ok:
                digest_candidates.append({
                    "ticker": o.ticker, "rr_at_market": o.rr_at_market, "pb": bool(o.pb_funnel),
                    "stop_width_atr": o.stop_width_atr, "reasons": fails,
                })

        risk_events = []
        for o in onsets:
            risk_events.extend(evaluate_risk(
                ticker=o.ticker, planned_stop=o.stop, today_close=o.close,
                action_code=o.action_code, ext_z=o.ext_z, date=date,
            ))
        out = {
            "dry_run": True,
            "date": date,
            "onsets_scanned": len(onsets),
            "entry_pushed": pushes,
            "entry_digest_candidates": digest_candidates,
            "risk_events": [{"ticker": e.ticker, "kind": e.kind, "message": e.message}
                            for e in risk_events],
        }
        digest = run_digest(date=date, notify_enabled=False)
        out["income_candidates"] = digest["income_candidates"]
        out["income_blocked_climax"] = digest["income_blocked_climax"]
        out["digest_body"] = digest["digest_body"]
    else:
        entry_out = run_daily_alert_sweep(date=date)
        digest_out = run_digest(date=date)
        # Merge, don't update(): both dicts carry entry_pushed and run_digest re-runs fire_entry,
        # whose pushes are dedupe-suppressed, so a plain update() would report [] and hide the
        # pushes that the sweep had just made.
        out = {
            "dry_run": False,
            "date": date,
            "onsets_scanned": entry_out.get("onsets_scanned", 0),
            "entry_pushed": entry_out.get("entry_pushed", []),
            "entry_digest_candidates": entry_out.get("entry_digest_candidates", []),
            "risk_pushed": entry_out.get("risk_pushed", []),
            "risk_events": entry_out.get("risk_events", []),
            "income_candidates": digest_out.get("income_candidates", []),
            "income_blocked_climax": digest_out.get("income_blocked_climax", []),
            "income_pushed": digest_out.get("income_pushed", []),
            "digest_sent": digest_out.get("digest_sent", False),
        }

    out["ops"] = run_ops_checks(notify=not args.dry_run)
    _log_json({k: v for k, v in out.items() if k != "digest_body"})
    if args.dry_run:
        print("\n" + "=" * 70 + "\nDIGEST BODY (dry run)\n" + "=" * 70)
        print(out.get("digest_body", ""))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(description="Alert tiers: ENTRY / RISK / INCOME / OPS / DIGEST")
    p.add_argument("--date", help="YYYY-MM-DD. Defaults to today.")
    p.add_argument("--loop", action="store_true", help="Run OPS on a timer until interrupted.")
    p.add_argument("--interval", type=int, default=300, help="Seconds between OPS passes in --loop.")
    p.add_argument("--ops-only", action="store_true", help="Run only the OPS health checks.")
    p.add_argument("--digest", action="store_true", help="Run only the digest + INCOME tier.")
    p.add_argument("--dry-run", action="store_true", help="Compute and print; send nothing.")
    p.add_argument("--history", type=int, default=0, metavar="N",
                   help="Print the last N notifications and exit.")
    p.add_argument("--purge-test-rows", action="store_true",
                   help="Delete fixture tickers from the ledger. Destructive; dry-run by default.")
    p.add_argument("--confirm-purge", action="store_true",
                   help="Required with --purge-test-rows to actually delete.")
    args = p.parse_args(argv)

    if args.history:
        _log_json(sent_history(args.history))
        return 0

    if args.purge_test_rows:
        result = purge_test_rows(dry_run=not args.confirm_purge)
        _log_json(result)
        if not args.confirm_purge:
            print("\nDry run. Re-run with --confirm-purge to delete.")
        return 0

    if args.ops_only:
        return run_ops(args)

    if args.digest:
        date = args.date or datetime.now().strftime("%Y-%m-%d")
        _log_json({k: v for k, v in run_digest(date=date).items() if k != "digest_body"})
        return 0

    if args.loop:
        purge_dedupe(older_than_days=30)
        last_digest_date = None
        try:
            while True:
                run_ops_checks(notify=not args.dry_run)
                today = datetime.now().strftime("%Y-%m-%d")
                # Once per session, not once per tick. run_digest fires INCOME with force=True
                # (it runs after the close, outside the push window), so re-running it every
                # interval would re-page every INCOME signal every few minutes all evening.
                if is_digest_time() and today != last_digest_date:
                    run_digest(date=today)
                    last_digest_date = today
                time.sleep(max(30, args.interval))
        except KeyboardInterrupt:
            logger.info("alert loop stopped")
            return 0

    # Default: the after-close pass.
    return run_close_pass(args)


if __name__ == "__main__":
    sys.exit(main())