"""High-impact US economic releases, matched against open option positions.

Source: the free Forex Factory weekly JSON (this week + next week). Nothing here predicts direction; it only
tells you a scheduled release lands before an option you hold expires, early enough to act while the market is open.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from src import config

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MT = ZoneInfo("America/Denver")
URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
CACHE_S = 3600
LEADS_H = (24, 1)
EXPOSURE_DAYS = 7
SETTLE_ET = (16, 0)


def _cache_path():
    return config.BASE_DIR / "data" / "macro_calendar.json"


def fetch_events(now: datetime) -> List[Dict[str, Any]]:
    path = _cache_path()
    if path.exists() and now.timestamp() - path.stat().st_mtime < CACHE_S:
        return json.loads(path.read_text())
    raw: List[Dict[str, Any]] = []
    for url in URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw += json.loads(r.read().decode("utf-8"))
        except Exception as e:
            logger.warning("macro calendar fetch failed for %s: %s", url, e)
    if not raw:
        return json.loads(path.read_text()) if path.exists() else []
    events = [{"title": e["title"], "at": e["date"], "forecast": e.get("forecast", ""), "previous": e.get("previous", "")}
              for e in raw if e.get("country") == "USD" and e.get("impact") == "High"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events))
    return events


PLAYBOOK = (
    ("PPI", "Hotter than forecast: yields up, pressure on NDX.", "Cooler than forecast: yields down, support for NDX."),
    ("CPI", "Hotter than forecast: yields up, pressure on NDX.", "Cooler than forecast: yields down, support for NDX."),
    ("Retail Sales", "Stronger: good for growth but adds rate fear if PPI is also hot.", "Weaker: growth scare, mixed unless inflation is cool."),
    ("Speaks", "Hawkish tone: pressure.", "Dovish tone: support."),
)


def _playbook(titles: List[str]) -> List[Dict[str, str]]:
    """Reading guide per release family. Unmeasured policy text, not a forecast."""
    out, seen = [], set()
    for t in titles:
        for key, hot, cool in PLAYBOOK:
            if key in t and key not in seen:
                seen.add(key)
                out.append({"release": key, "hot": hot, "cool": cool})
    return out


def _option_expiry(p: Dict[str, Any]):
    exp = str(p.get("option_expiration") or "")[:10]
    try:
        return datetime.strptime(exp, "%Y-%m-%d").date()
    except ValueError:
        return None


def release_groups(now: datetime, events: List[Dict[str, Any]], positions: List[Dict[str, Any]], horizon_h: float = 48) -> List[Dict[str, Any]]:
    """Upcoming releases grouped by timestamp, each with the open options (expiring within a week of it) it can hit."""
    options = [(p, _option_expiry(p)) for p in positions if p.get("asset_type") == "OPTION" and p.get("quantity")]
    options = [(p, e) for p, e in options if e]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        groups.setdefault(e["at"], []).append(e)
    out = []
    for at, evs in sorted(groups.items()):
        when = datetime.fromisoformat(at).astimezone(ET)
        hours = (when - now.astimezone(ET)).total_seconds() / 3600
        if not 0 < hours <= horizon_h:
            continue
        exposed: Dict[tuple, int] = {}
        for p, exp in options:
            if (exp > when.date() or (exp == when.date() and (when.hour, when.minute) < SETTLE_ET)) and exp <= when.date() + timedelta(days=EXPOSURE_DAYS):
                k = ((p.get("underlying_symbol") or p.get("symbol") or "").upper(), exp.isoformat())
                exposed[k] = exposed.get(k, 0) + 1
        out.append({
            "at": at, "hours": round(hours, 1),
            "when": f"{when:%a %b %d %H:%M} ET ({when.astimezone(MT):%H:%M} MT)",
            "pre_open": (when.hour, when.minute) < (9, 30),
            "releases": [{"title": e["title"], "forecast": e.get("forecast", ""), "previous": e.get("previous", "")} for e in evs],
            "exposed": [{"underlying": u, "expiry": x, "legs": n} for (u, x), n in sorted(exposed.items(), key=lambda i: i[0][1])],
            "playbook": _playbook([e["title"] for e in evs]),
            "same_day": any(x == when.date().isoformat() for _, x in exposed),
        })
    return out


def opex_status(now: datetime, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Monthly opex (third Friday) during its own week, plus the open option legs expiring that day. Timing only."""
    from src.clients.macro_client import _third_friday

    today = now.astimezone(ET).date()
    opex = _third_friday(today.year, today.month)
    if not 0 <= (opex - today).days <= 4 or today.weekday() > opex.weekday():
        return {}
    legs = []
    for p in positions:
        exp = _option_expiry(p) if p.get("asset_type") == "OPTION" and p.get("quantity") else None
        if exp == opex:
            legs.append({"underlying": (p.get("underlying_symbol") or p.get("symbol") or "").upper(),
                         "strike": p.get("option_strike"), "type": str(p.get("option_type") or "").upper(),
                         "qty": p.get("quantity")})
    return {"date": opex.isoformat(), "days": (opex - today).days, "quarterly": opex.month in (3, 6, 9, 12), "legs": legs}


def macro_events(now: datetime, events: List[Dict[str, Any]], positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for g in release_groups(now, events, positions, horizon_h=max(LEADS_H)):
        if not g["exposed"]:
            continue
        names = ", ".join(sorted({r["title"] for r in g["releases"]}))
        legs = "; ".join(f"{x['underlying']} exp {x['expiry']} ({x['legs']} leg{'s' if x['legs'] != 1 else ''})" for x in g["exposed"])
        tail = "It lands before the open, so you can only react from 9:30 ET." if g["pre_open"] else "Decide before it prints."
        lead = min(h for h in LEADS_H if g["hours"] <= h)
        out.append({"kind": "MACRO", "severity": "WARN" if g["same_day"] else "INFO", "ticker": None,
                    "key": f"MACRO:{g['at']}:{lead}h",
                    "title": f"Economic data {g['when']}: {names}",
                    "body": f"Open options exposed: {legs}. {tail}"})
    return out


def context_text(now: datetime, events: List[Dict[str, Any]], positions: List[Dict[str, Any]]) -> str:
    """Prompt block for the LLM: when releases land and which open options they can hit. Timing, not direction."""
    groups = release_groups(now, events, positions)
    if not groups:
        return ""
    lines = []
    for g in groups:
        rel = ", ".join(f"{r['title']} (fcst {r['forecast'] or 'n/a'}, prev {r['previous'] or 'n/a'})" for r in g["releases"])
        exp = "; ".join(f"{x['underlying']} exp {x['expiry']} ({x['legs']} leg{'s' if x['legs'] != 1 else ''})" for x in g["exposed"])
        lines.append(f"- {g['when']} in {g['hours']}h: {rel}." + (f" Open options exposed: {exp}." if exp else ""))
    return ("[ECONOMIC RELEASES, NEXT 48H]\n" + "\n".join(lines) +
            "\nUse this for timing and exposure only. The release type carries no measured directional edge; do not predict direction from it. "
            "If an open option expires on or soon after a release, say so and make the hold-or-close decision explicit before the print.")
