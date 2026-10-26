"""
src/tracking/plan_repair.py — one-time sweep + read repair for dropped breakouts.

The old gate stripped a breakout trigger whose targets sat at/below it: it wrote
``breakout_level = 0`` and kept the trigger only inside the recorded demotion
reason. Two consequences this module fixes:

  1. A one-time sweep rebuilds and re-arms plans already stored that way — the
     stored GOOGL plan (trigger $356.83, T1 $356.73) is exactly this case, so it
     arms without re-running research.
  2. The same repair runs on plan read: a stored trigger that is still present
     but whose targets sit at/below it is rebuilt above it before the levels are
     armed, never zeroed.

Repair is fail-closed: a trigger that cannot be recovered from a real number (the
reason text carries no value) is left alone — absent means absent, never invented.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import config

logger = logging.getLogger(__name__)

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _num(val: Any) -> float:
    if val is None or val == "" or val == "N/A":
        return 0.0
    try:
        f = float(val)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def repair_shares_plan(
    sp: Dict[str, Any],
    ticker: str = "",
    date_str: str = "",
    atr: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Repair one shares_plan's breakout path in place. Returns a change report,
    or None when there is nothing to repair.

    Two broken shapes are handled:
      * the trigger was zeroed — it is restored from the recorded demotion reason;
      * T1 or T2 sits at/below the trigger — the targets are rebuilt strictly above
        it (next resistance, else an ATR multiple, else the plan's own risk) and the
        plan is tagged ``targets_rebuilt`` with its basis.

    A trigger whose value is not recoverable from any real number is left alone:
    the plan keeps the path it has, and the report says why.
    """
    if not isinstance(sp, dict):
        return None

    from src.tracking.level_engine import restore_demoted_trigger

    trigger = _num(sp.get("breakout_level"))
    restored_from_reason = False
    if trigger <= 0:
        trigger, reason = restore_demoted_trigger(sp)
        restored_from_reason = trigger > 0
        if not restored_from_reason:
            # The trigger value was never recorded: nothing real to restore.
            return None

    t1 = _num(sp.get("target_1"))
    t2 = _num(sp.get("target_2"))
    zone_lo, zone_hi = _num(sp.get("entry_zone_low")), _num(sp.get("entry_zone_high"))
    dual = zone_lo > 0 and zone_hi > 0 and str(sp.get("entry_type") or "").upper() != "BREAKOUT"
    needs_targets = (t1 > 0 and t1 <= trigger) or (t2 > 0 and t2 <= trigger)
    if dual and _num(sp.get("breakout_target_1")) > trigger:
        needs_targets = False
    if not restored_from_reason and not needs_targets:
        return None

    change: Dict[str, Any] = {
        "ticker": (ticker or sp.get("ticker") or "").upper(),
        "breakout_level": round(trigger, 4),
        "restored_trigger": restored_from_reason,
    }

    if restored_from_reason:
        sp["breakout_level"] = round(trigger, 4)
        bo_stop = _num(sp.get("breakout_stop"))
        if bo_stop <= 0:
            stop = _num(sp.get("tactical_stop"))
            # A trigger stop is only reused from the plan's own stop when it sits
            # below the trigger; otherwise it stays absent (never invented).
            if 0 < stop < trigger:
                sp["breakout_stop"] = stop

    if needs_targets:
        from src.logic.level_validation import _rebuild_targets_above_trigger

        new_t1, new_t2, basis = _rebuild_targets_above_trigger(
            trigger=trigger,
            old_t1=t1,
            old_t2=t2,
            stop=_num(sp.get("tactical_stop")),
            atr=_num(atr),
            ticker=(ticker or sp.get("ticker") or ""),
            date_str=date_str or "",
        )
        if new_t1 > trigger and new_t2 > new_t1:
            if dual:
                sp["breakout_target_1"] = new_t1
                sp["breakout_target_2"] = new_t2
            else:
                sp["target_1"] = new_t1
                sp["target_2"] = new_t2
            sp["targets_rebuilt"] = True
            sp["targets_rebuilt_basis"] = basis
            reason = sp.get("breakout_demoted_reason")
            tag = (
                f"repair: trigger ${trigger:.4f} re-armed, targets rebuilt above it via {basis}"
            )
            sp["breakout_demoted_reason"] = f"{reason}; {tag}" if reason else tag
            change.update(
                {
                    "target_1": new_t1,
                    "target_2": new_t2,
                    "targets_rebuilt": True,
                    "targets_rebuilt_basis": basis,
                }
            )
        else:
            change["targets_rebuilt"] = False
            change["note"] = "no real resistance/ATR/risk basis to rebuild targets above the trigger"
    return change


def repair_watch_levels_dict(
    data: Dict[str, Any], ticker: str = "", date_str: str = "", atr: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """Repair a whole *_watch_levels.json document's shares_plan."""
    sp = (data or {}).get("shares_plan")
    if not isinstance(sp, dict):
        return None
    change = repair_shares_plan(
        sp,
        ticker=ticker or str(data.get("ticker") or ""),
        date_str=date_str or str(data.get("date") or ""),
        atr=atr,
    )
    return change


def _stored_plan_files() -> List[Tuple[Path, str, str]]:
    """Every stored watch-levels file: (path, ticker, date)."""
    files: List[Tuple[Path, str, str]] = []
    roots = [
        config.BASE_DIR / "reports",
        config.BASE_DIR / "data" / "raw",
        config.BASE_DIR / "data" / "triage",
    ]
    for root in roots:
        if not root.exists():
            continue
        for p in root.glob("**/*_watch_levels.json"):
            date_str = ""
            for parent in p.parents:
                if _DATE_DIR_RE.match(parent.name):
                    date_str = parent.name
                    break
            m = re.match(r"^(.+)_watch_levels\.json$", p.name)
            files.append((p, (m.group(1) if m else "").upper(), date_str))
    return files


def sweep_stored_plans(write: bool = True, base_dir: Optional[Path] = None, reconcile_rows: bool = False) -> Dict[str, Any]:
    """The one-time sweep: rebuild and re-arm every stored plan with a dropped or
    broken breakout. Returns a report (repaired / skipped / errors) and, when
    ``write`` is true, persists the repaired plans back to their file and DB row.

    Files are grouped by (ticker, date): the same plan is stored under both
    reports/<date>/ and data/raw/<date>/<T>/, and the repair (which may fetch daily
    bars for the next-resistance basis) runs once per plan, not once per copy.

    ``reconcile_rows`` additionally enforces the stored plan's trigger on any active
    watch_targets row that disagrees with it (the GOOGL 355-vs-356.83 case). It is
    opt-in because a row can hold a deliberate manual override.
    """
    if base_dir is not None:
        config.BASE_DIR = base_dir

    groups: Dict[Tuple[str, str], List[Path]] = {}
    for path, ticker, date_str in _stored_plan_files():
        groups.setdefault((ticker, date_str), []).append(path)

    repaired: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for (ticker, date_str), paths in sorted(groups.items()):
        try:
            docs = []
            for path in paths:
                try:
                    docs.append((path, json.loads(path.read_text(encoding="utf-8"))))
                except Exception as e:
                    errors.append({"file": str(path), "error": str(e)})
            if not docs:
                continue
            _first_path, first_doc = docs[0]
            sp = first_doc.get("shares_plan")
            if not isinstance(sp, dict):
                continue
            trigger = _num(sp.get("breakout_level"))
            if trigger > 0:
                t1, t2 = _num(sp.get("target_1")), _num(sp.get("target_2"))
                already_sound = not ((t1 > 0 and t1 <= trigger) or (t2 > 0 and t2 <= trigger))
            else:
                already_sound = not sp.get("breakout_demoted_reason")
            if already_sound:
                continue  # trigger intact and targets above it — nothing to repair

            change = repair_watch_levels_dict(first_doc, ticker=ticker, date_str=date_str)
            if change is None:
                skipped.append({
                    "ticker": ticker,
                    "date": date_str,
                    "files": [str(p) for p in paths],
                    "reason": "trigger not recoverable from a recorded number — left as stored",
                })
                continue
            if not (change.get("restored_trigger") or change.get("targets_rebuilt")):
                skipped.append({
                    "ticker": ticker,
                    "date": date_str,
                    "files": [str(p) for p in paths],
                    "reason": change.get("note") or "nothing to rebuild above the trigger",
                })
                continue
            repaired.append(dict(change, date=date_str, files=[str(p) for p in paths]))
            if write:
                repaired_sp = first_doc.get("shares_plan") or {}
                for path, doc in docs:
                    if isinstance(doc.get("shares_plan"), dict):
                        doc["shares_plan"] = repaired_sp
                    try:
                        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
                    except Exception as e:
                        errors.append({"file": str(path), "ticker": ticker, "error": f"write failed: {e}"})
        except Exception as e:
            errors.append({"ticker": ticker, "error": str(e)})

    db_changes = repair_watch_targets(write=write, reconcile_rows=reconcile_rows)
    reconciled_rows = [r for r in db_changes.get("repaired", []) if r.get("kind") == "reconciled"]

    return {
        "repaired": repaired,
        "skipped": skipped,
        "watch_targets": db_changes,
        "errors": errors,
        "summary": (
            f"{len(repaired)} stored plan(s) repaired across {sum(len(r['files']) for r in repaired)} file(s), "
            f"{len(db_changes['repaired'])} watch_targets row(s) repaired"
            + (f" ({len(reconciled_rows)} reconciled to the stored plan's trigger)" if reconciled_rows else "")
            + f", {len(skipped)} skipped (trigger not recoverable), {len(errors)} error(s)"
        ),
    }


def _stored_plan_for(ticker: str, date_str: str) -> Optional[Dict[str, Any]]:
    """The shares_plan of the stored plan file for one (ticker, date), if any."""
    if not ticker or not date_str:
        return None
    safe = ticker.replace(":", "_").upper()
    candidates = [
        config.BASE_DIR / "reports" / date_str / f"{safe}_watch_levels.json",
        config.BASE_DIR / "data" / "raw" / date_str / safe / f"{safe}_watch_levels.json",
        config.BASE_DIR / "data" / "triage" / date_str / "_DEEP_RESEARCH" / safe / f"{safe}_watch_levels.json",
        config.BASE_DIR / "data" / "triage" / date_str / "force" / safe / f"{safe}_watch_levels.json",
    ]
    for path in candidates:
        try:
            if not path.exists():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            sp = doc.get("shares_plan")
            if isinstance(sp, dict):
                return sp
        except Exception:
            continue
    return None


def _apply_plan_to_row(row: Dict[str, Any], shares_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild a row's plan fields from the stored plan of record."""
    rj_raw = row.get("raw_json")
    rj = json.loads(rj_raw) if isinstance(rj_raw, str) and rj_raw else {}
    if not isinstance(rj, dict):
        rj = {}
    merged = {k: v for k, v in shares_plan.items() if v not in (None, "")}
    data = dict(row)
    rj["shares_plan"] = merged
    data["raw_json"] = json.dumps(rj, default=str)
    data["shares_plan"] = merged
    data["force_update"] = True
    for key in ("entry_zone_low", "entry_zone_high", "breakout_level", "breakout_stop", "tactical_stop", "target_1", "target_2"):
        if merged.get(key) is not None:
            data[key] = merged.get(key)
    return data


def reconcile_row_with_plan(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Reconcile one watch_targets row with the stored plan of record for its date.

    THE PLAN WINS. The plan file is the arbitration's binding directive for that name
    and date; a row that carries a different breakout level arms a level the research
    never authorized. The live GOOGL case is exactly this: the row's 355.00 is the
    options put wall (a gamma level from the summary's GEX block), while the plan's
    trigger is 356.83 — so the level that alerts is 356.83.

    This is opt-in (``reconcile_rows=True``): a row can also hold a deliberate manual
    override, and overwriting that is a data change, never a background side effect.
    """
    ticker = str(row.get("ticker") or "").upper()
    date = str(row.get("date") or "")
    status = str(row.get("status") or "").upper()
    if not ticker or not date:
        return None
    if status in ("CLOSED", "INVALIDATED", "TARGET_HIT"):
        return None
    shares_plan = _stored_plan_for(ticker, date)
    if not shares_plan:
        return None
    plan_trigger = _num(shares_plan.get("breakout_level"))
    if plan_trigger <= 0:
        # The plan itself has no trigger to reconcile against (a zeroed-and-
        # unrecoverable plan) — leave the row alone rather than invent a level.
        return None
    row_trigger = _num(row.get("breakout_level"))
    if abs(row_trigger - plan_trigger) < 0.005:
        return None  # they already agree
    return {
        "ticker": ticker,
        "date": date,
        "row_breakout_level": row_trigger,
        "plan_breakout_level": plan_trigger,
        "reason": (
            f"the row's trigger ${row_trigger} disagrees with the plan's ${plan_trigger}; "
            "the stored plan (the arbitration directive) is the level that alerts"
        ),
        "_data": _apply_plan_to_row(row, shares_plan),
    }


def repair_watch_targets(write: bool = True, reconcile_rows: bool = False) -> Dict[str, Any]:
    """Repair stored watch_targets rows whose trigger is broken (targets at/below
    the trigger, or a dropped breakout recorded in the row's shares_plan).

    Only ACTIVE rows are repaired: a closed / invalidated history row is a record
    of a finished position, not a live plan, and the write gate refuses it (its
    entry_type is usually NO_ENTRY) — re-deriving a repair for it every run would
    never persist. Those rows are reported as skipped with their reason.

    ``reconcile_rows`` makes the stored plan's trigger win where the row disagrees
    with it (the GOOGL 355-vs-356.83 case); it is opt-in because a row can hold a
    deliberate manual override.
    """
    repaired: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        from src.tracking import watch_manager

        rows = watch_manager.get_all_watch_targets()
    except Exception as e:
        return {"repaired": [], "skipped": [], "errors": [{"error": f"watch_targets read failed: {e}"}]}

    for row in rows:
        ticker = str(row.get("ticker") or "")
        date = str(row.get("date") or "")
        status = str(row.get("status") or "").upper()
        is_active = row.get("is_active")
        if (is_active == 0) or status in ("CLOSED", "INVALIDATED", "TARGET_HIT"):
            skipped.append({
                "ticker": ticker,
                "date": date,
                "reason": f"history row (status={status or 'UNKNOWN'}, is_active={is_active}) — a finished position is not re-armed",
            })
            continue

        if reconcile_rows:
            try:
                recon = reconcile_row_with_plan(row)
            except Exception as e_rec:
                recon = None
                errors.append({"ticker": ticker, "date": date, "error": f"reconcile failed: {e_rec}"})
            if recon:
                entry = {k: v for k, v in recon.items() if k != "_data"}
                if write:
                    data = recon["_data"]
                    before_trigger = row.get("breakout_level")
                    watch_manager.upsert_watch_target(data)
                    try:
                        after = next(
                            (r for r in watch_manager.get_all_watch_targets()
                             if r.get("ticker") == ticker and str(r.get("date") or "") == date),
                            None,
                        )
                    except Exception:
                        after = None
                    entry["persisted"] = bool(
                        after is not None
                        and abs(_num(after.get("breakout_level")) - _num(entry["plan_breakout_level"])) < 0.005
                    )
                    if not entry["persisted"]:
                        entry["note"] = "reconciled plan did not persist — row left as stored"
                repaired.append(dict(entry, kind="reconciled"))
                continue

        try:
            raw = row.get("raw_json")
            rj = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
            sp = rj.get("shares_plan") if isinstance(rj, dict) else None
            if not isinstance(sp, dict):
                sp = {}
                rj = rj if isinstance(rj, dict) else {}
            # The row's own columns are the plan of record; mirror the file shape.
            # A zeroed value in shares_plan falls back to the row column and back.
            merged = dict(sp)
            for key in (
                "breakout_level", "breakout_stop", "tactical_stop", "target_1", "target_2",
                "breakout_target_1", "breakout_target_2",
                "entry_zone_low", "entry_zone_high", "breakout_demoted_reason",
                "targets_rebuilt", "targets_rebuilt_basis",
            ):
                if _num(merged.get(key)) <= 0 and key not in ("breakout_demoted_reason", "targets_rebuilt", "targets_rebuilt_basis"):
                    if _num(row.get(key)) > 0:
                        merged[key] = row.get(key)
                elif merged.get(key) in (None, "") and row.get(key) not in (None, ""):
                    merged[key] = row.get(key)

            change = repair_shares_plan(
                merged, ticker=str(row.get("ticker") or ""), date_str=str(row.get("date") or "")
            )
            if change is None:
                continue
            entry = dict(change, ticker=str(row.get("ticker") or ""), date=str(row.get("date") or ""))
            repaired.append(entry)
            if write:
                data = dict(row)
                rj = dict(rj) if isinstance(rj, dict) else {}
                rj["shares_plan"] = merged
                data["raw_json"] = json.dumps(rj, default=str)
                # upsert_watch_target reads the plan from the top-level shares_plan,
                # so the repaired plan must be there or the row is re-gated to zero.
                data["shares_plan"] = merged
                # The repair must land: an open unexpired thesis would otherwise be
                # kept as-is by the no-overwrite guard.
                data["force_update"] = True
                data["breakout_level"] = merged.get("breakout_level") or 0.0
                data["breakout_stop"] = merged.get("breakout_stop") or 0.0
                data["target_1"] = merged.get("target_1")
                data["target_2"] = merged.get("target_2")
                data["breakout_target_1"] = merged.get("breakout_target_1")
                data["breakout_target_2"] = merged.get("breakout_target_2")
                before_t1 = row.get("target_1")
                watch_manager.upsert_watch_target(data)
                # upsert_watch_target returns None either way, so confirm the repair
                # actually landed: a row whose write the watch gate kept out is
                # reported as skipped rather than re-derived on every run.
                try:
                    after = next(
                        (r for r in watch_manager.get_all_watch_targets()
                         if r.get("ticker") == ticker and str(r.get("date") or "") == date),
                        None,
                    )
                except Exception:
                    after = None
                if after is not None and after.get("target_1") == before_t1 and before_t1 != merged.get("target_1"):
                    skipped.append({
                        "ticker": ticker,
                        "date": date,
                        "reason": "write gate kept the repair out — row left as stored",
                    })
                    continue
        except Exception as e:
            errors.append({"ticker": row.get("ticker"), "error": str(e)})

    return {"repaired": repaired, "skipped": skipped, "errors": errors}


if __name__ == "__main__":  # python -m src.tracking.plan_repair [--dry-run] [--reconcile-rows]
    import sys

    logging.basicConfig(level=logging.INFO)
    argv = sys.argv[1:]
    # An unrecognised flag is NOT a write run: repairs are opt-in (--write), so a
    # stray invocation like --help or a typo reports a dry run instead of writing.
    dry = "--dry-run" in argv or "--write" not in argv
    report = sweep_stored_plans(write=not dry, reconcile_rows="--reconcile-rows" in argv)
    print(json.dumps(report, indent=2, default=str))
