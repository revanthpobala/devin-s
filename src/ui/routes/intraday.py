"""
Intraday trading, open positions state, flattening, and 0DTE simulation endpoints.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.ui.state import POSITIONS_FILE, append_log

logger = logging.getLogger("ui_server")
router = APIRouter(tags=["intraday"])


class Simulate0DTERequest(BaseModel):
    ticker: str
    action: str = "ENTER_CALLS"
    price: float = 560.0
    why_now: str = "15m Squeeze Breakout + VWAP reclaim"


@router.get("/api/positions")
def get_positions():
    """Fetch open positions with live quotes and PnL."""
    if not POSITIONS_FILE.exists():
        return {"positions": []}
    try:
        from src.clients.price_client import get_current_price
        today_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            pos_dict = raw_data.get("positions", raw_data) if isinstance(raw_data, dict) else {}
            positions = []
            for sym, p in pos_dict.items():
                if not isinstance(p, dict):
                    continue
                item = dict(p)
                opened_at = item.get("opened_at", "")
                if opened_at and opened_at[:10] != today_str:
                    continue
                if str(item.get("raw_alert", {}).get("subject", "")).lower() == "alert: screener":
                    continue

                entry = item.get("entry_price") or item.get("alert_price") or 0.0
                try:
                    spot = get_current_price(sym, context="execution")
                    if isinstance(spot, (int, float)) and spot > 0:
                        item["current_price"] = spot
                        side = item.get("side", "LONG").upper()
                        if entry > 0:
                            if "LONG" in side or "BUY" in side or "CALL" in side:
                                item["pnl_pct"] = round(((spot - entry) / entry) * 100, 2)
                            else:
                                item["pnl_pct"] = round(((entry - spot) / entry) * 100, 2)
                except Exception:
                    pass
                positions.append(item)
            return {"positions": positions}
    except Exception as e:
        return {"positions": [], "error": str(e)}


@router.get("/api/intraday/signals")
def get_intraday_signals_endpoint():
    """Fetch today's Grade-A 0DTE signals, tape status, and executed plays."""
    import sqlite3
    from src import config
    from src.clients.price_client import get_current_price

    db_path = config.alerts_db_path()
    if not db_path.exists():
        return {"signals": [], "count": 0, "active_count": 0, "targets_hit": 0}

    now_et = datetime.now(ZoneInfo("America/New_York"))
    today_str = now_et.strftime("%Y-%m-%d")

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cols = [c[1] for c in cur.execute("PRAGMA table_info(intraday_signals)").fetchall()]

        # Try today first, fallback to most recent date if no signals today
        rows = cur.execute(
            "SELECT * FROM intraday_signals WHERE date = ? ORDER BY id DESC",
            (today_str,)
        ).fetchall()
        
        active_date = today_str
        if not rows:
            latest_row = cur.execute("SELECT date FROM intraday_signals ORDER BY id DESC LIMIT 1").fetchone()
            if latest_row:
                active_date = latest_row[0]
                rows = cur.execute(
                    "SELECT * FROM intraday_signals WHERE date = ? ORDER BY id DESC",
                    (active_date,)
                ).fetchall()

        # Load open positions
        open_pos_map = {}
        if POSITIONS_FILE.exists():
            try:
                with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                    pos_data = json.load(f)
                    pos_dict = pos_data.get("positions", pos_data) if isinstance(pos_data, dict) else {}
                    open_pos_map = {k: v for k, v in pos_dict.items() if isinstance(v, dict)}
            except Exception:
                pass

        # Load exits for active_date
        exit_alerts = cur.execute(
            "SELECT symbol, alert_price, timestamp, llm_decision FROM alerts WHERE substr(timestamp, 1, 10)=? AND UPPER(action)='EXIT' ORDER BY timestamp DESC",
            (active_date,)
        ).fetchall()
        exit_map = {r[0]: {"exit_price": r[1], "timestamp": r[2], "decision": r[3]} for r in exit_alerts}

        signals = []
        targets_hit = 0
        active_count = 0

        for r in rows:
            d = dict(zip(cols, r))
            sym = d["ticker"]
            entry = float(d.get("entry_price") or d.get("entry_px") or 0.0)
            t1 = float(d.get("target_1") or d.get("entry_t1") or 0.0) if (d.get("target_1") or d.get("entry_t1")) else None
            stop = float(d.get("stop") or d.get("entry_stop") or 0.0) if (d.get("stop") or d.get("entry_stop")) else None
            side = (d.get("side") or "LONG").upper()
            is_long = "LONG" in side or "BUY" in side or "CALL" in side

            # Live spot
            try:
                spot = get_current_price(sym, context="execution")
                spot = round(float(spot), 2) if spot and spot > 0 else entry
            except Exception:
                spot = entry

            # P&L
            pnl_pct = 0.0
            if entry > 0 and spot > 0:
                pnl_pct = round(((spot - entry) / entry) * 100, 2) if is_long else round(((entry - spot) / entry) * 100, 2)

            # Status determination
            ex_info = exit_map.get(sym)
            is_open = sym in open_pos_map

            status = "MONITORING"
            status_badge = "amber"
            status_text = "Triggered"

            if is_open:
                status = "ACTIVE_OPEN"
                status_badge = "green"
                status_text = "🟢 Open Position"
                active_count += 1
            elif t1 and ((is_long and spot >= t1) or (not is_long and spot <= t1)):
                status = "TARGET_HIT"
                status_badge = "cyan"
                status_text = "🏁 Target 1 Hit"
                targets_hit += 1
            elif ex_info:
                status = "EXITED"
                status_badge = "red"
                status_text = "🛑 Position Exited"
            elif entry > 0 and pnl_pct > 0:
                status = "IN_PROFIT"
                status_badge = "emerald"
                status_text = "📈 In Profit"

            # 0DTE Contract Suggestion
            contract = f"${sym} 0DTE ATM {'Calls' if is_long else 'Puts'}"

            signals.append({
                "ticker": sym,
                "side": side,
                "contract": contract,
                "grade": d.get("grade") or "A",
                "score": d.get("score") or 85,
                "entry_price": entry,
                "spot_price": spot,
                "stop_price": stop,
                "target_1": t1,
                "pnl_pct": pnl_pct,
                "status": status,
                "status_badge": status_badge,
                "status_text": status_text,
                "llm_verdict": d.get("llm_verdict") or "TAKE",
                "exit_info": ex_info,
                "date": active_date,
                "is_open": is_open,
                "open_pos_details": open_pos_map.get(sym)
            })

        conn.close()
        return {
            "signals": signals,
            "count": len(signals),
            "active_count": active_count,
            "targets_hit": targets_hit,
            "date": active_date,
            "is_today": active_date == today_str
        }
    except Exception as e:
        logger.error(f"Error fetching intraday signals: {e}")
        return {"signals": [], "count": 0, "error": str(e)}


@router.get("/api/schwab/positions")
def get_schwab_positions_endpoint():
    """Fetch live broker account balances and positions from Schwab Trader API."""
    try:
        from src.clients.schwab_client import get_schwab_positions
        return get_schwab_positions()
    except Exception as e:
        return {"status": "error", "error": str(e), "accounts": []}


@router.post("/api/positions/{ticker}/close")
def close_open_position(ticker: str):
    """Close an open position from data/positions.json."""
    try:
        from src.tracking.position_state import close_position
        rec = close_position(ticker)
        if rec:
            append_log(f"🛑 Closed open position for {ticker.upper()}.")
            return {"success": True, "closed": rec}
        else:
            return {"success": False, "message": f"{ticker} was not open."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/positions/flatten-intraday")
def flatten_intraday_endpoint():
    """Flatten and close all intraday positions for EOD."""
    try:
        from src.tracking.position_state import flatten_eod_intraday_positions
        closed = flatten_eod_intraday_positions(force=True)
        append_log(f"🧹 EOD Flattened {len(closed)} intraday position(s).")
        return {"success": True, "count": len(closed), "closed": closed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/intraday/simulate-eval")
def simulate_0dte_eval(req: Simulate0DTERequest):
    """Simulate and test 0DTE rules evaluation (revanth-0dte.md) on demand."""
    try:
        from main import query_local_llm_for_trade
        sym = req.ticker.strip().upper()
        fake_alert = {
            "ticker": sym,
            "action": req.action.strip().upper(),
            "price": req.price,
            "why_now": req.why_now,
            "strategy": "Simulated 0DTE",
            "time": datetime.now().isoformat(),
        }
        decision, playbook = query_local_llm_for_trade(fake_alert, sym, "Simulated 0DTE")
        return {"status": "ok", "decision": decision, "playbook": playbook}
    except Exception as e:
        logger.error(f"Error in 0DTE simulate eval: {e}")
        return {"status": "error", "error": str(e), "decision": "ERROR", "playbook": str(e)}


class SaveSkillRequest(BaseModel):
    skill_name: str
    content: str


@router.get("/api/skills")
def list_skills():
    """List all available on-demand skills from skills/."""
    from src import config
    skills_dir = config.BASE_DIR / "skills"
    if not skills_dir.exists():
        return {"skills": []}
    skills = []
    for f in sorted(skills_dir.glob("*.md")):
        skills.append({
            "name": f.stem,
            "filename": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "preview": f.read_text(encoding="utf-8")[:300]
        })
    return {"skills": skills}


@router.post("/api/skills/save")
def save_skill(req: SaveSkillRequest):
    """Save or update a skill markdown file with path traversal validation."""
    from src import config
    import re
    skills_dir = config.BASE_DIR / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    clean_name = req.skill_name.lower().strip().replace(".md", "")
    safe_name = Path(clean_name).name
    if not re.match(r'^[a-z0-9_\-]+$', safe_name):
        raise HTTPException(status_code=400, detail="Invalid skill name format")
    target = (skills_dir / f"{safe_name}.md").resolve()
    if not str(target).startswith(str(skills_dir.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal attempt blocked")
    target.write_text(req.content, encoding="utf-8")
    append_log(f"🧠 Updated Skill: {safe_name}.md ({len(req.content)} chars)")
    return {"status": "ok", "skill_name": safe_name, "path": str(target)}

