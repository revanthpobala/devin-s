"""Portfolio risk layer: exposure, SPY-beta, concentration, correlation, open-risk caps, hedge sizing, regime dial.

`compute_snapshot` is pure. `refresh_market_stats` pulls beta, sector and correlation from yfinance into a cache.
Nothing here is a measured edge: caps and dial are risk policy, and the hedge line is arithmetic.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from src import config

MAX_OPEN_RISK_PCT = 5.0
MAX_POSITIONS = 10
CORR_LINK = 0.7
MATERIAL_MV = 2000.0
CACHE_TTL_S = 12 * 3600
FUND_SECTORS = {"Fund/ETF"}
CASH_LIKE = {"SWVXX", "SPAXX", "FDRXX", "VMFXX", "SNSXX", "SWTXX"}

_lock = threading.Lock()
_snapshot: Dict[str, Any] = {}
_refreshing = False


def _cache_path():
    return config.BASE_DIR / "data" / "risk_cache.json"


def load_cache() -> Dict[str, Any]:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def refresh_market_stats(symbols: Iterable[str]) -> Dict[str, Any]:
    """Beta vs SPY (1y daily), sector and pairwise correlation (last 120 sessions). Cached 12h per symbol set."""
    import yfinance as yf

    syms = sorted({s for s in symbols if s and not s.startswith("$") and s not in CASH_LIKE})
    cache = load_cache()
    if cache.get("symbols") == syms and time.time() - cache.get("ts", 0) < CACHE_TTL_S:
        return cache
    out: Dict[str, Any] = {"ts": time.time(), "symbols": syms, "beta": {}, "sector": {}, "corr": {}}
    if syms:
        px = yf.download(syms + ["SPY"], period="1y", interval="1d", progress=False, auto_adjust=True)["Close"]
        rets = px.pct_change().dropna(how="all")
        spy = rets["SPY"]
        for s in syms:
            if s in rets:
                pair = rets[[s, "SPY"]].dropna()
                if len(pair) > 60 and pair["SPY"].var() > 0:
                    out["beta"][s] = round(float(pair[s].cov(pair["SPY"]) / pair["SPY"].var()), 2)
        tail = rets[[s for s in syms if s in rets]].tail(120)
        c = tail.corr()
        for i, a in enumerate(c.columns):
            for b in c.columns[i + 1:]:
                v = c.loc[a, b]
                if v == v:
                    out["corr"][f"{a}|{b}"] = round(float(v), 2)
        for s in syms:
            try:
                out["sector"][s] = yf.Ticker(s).info.get("sector") or "Fund/ETF"
            except Exception:
                out["sector"][s] = "Unknown"
    _cache_path().parent.mkdir(parents=True, exist_ok=True)
    _cache_path().write_text(json.dumps(out), encoding="utf-8")
    return out


def _clusters(names: List[str], corr: Dict[str, float]) -> List[List[str]]:
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k, v in corr.items():
        a, b = k.split("|")
        if a in parent and b in parent and v >= CORR_LINK:
            parent[find(a)] = find(b)
    groups: Dict[str, List[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return sorted((g for g in groups.values() if len(g) > 1), key=len, reverse=True)


def regime_dial(regime: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Size multiplier for NEW risk from SPY trend and VIX. Heuristic policy, not a measured breadth edge."""
    if not regime or not regime.get("available"):
        return {"multiplier": 0.5, "why": "market regime unavailable, size halved", "measured": False}
    bullish = bool((regime.get("tide") or {}).get("is_bullish"))
    vix = float((regime.get("vix") or {}).get("price") or 0.0)
    if bullish and vix < 20:
        m, why = 1.0, f"SPY trend up, VIX {vix:.1f}"
    elif bullish:
        m, why = 0.5, f"SPY trend up but VIX {vix:.1f} >= 20"
    elif vix < 30:
        m, why = 0.5, f"SPY trend down, VIX {vix:.1f}"
    else:
        m, why = 0.25, f"SPY trend down, VIX {vix:.1f} >= 30"
    return {"multiplier": m, "why": why, "measured": False}


def compute_snapshot(
    held: List[Dict[str, Any]],
    total_value: float,
    tracked: Dict[str, Dict[str, Any]],
    stats: Dict[str, Any],
    regime: Optional[Dict[str, Any]] = None,
    earnings_days: Optional[Dict[str, Optional[int]]] = None,
) -> Dict[str, Any]:
    book = float(total_value or 0.0)
    beta, sector, corr = stats.get("beta", {}), stats.get("sector", {}), stats.get("corr", {})
    names: Dict[str, Dict[str, Any]] = {}
    option_mv = 0.0
    cash_like = 0.0
    for p in held:
        und = p.get("underlying_symbol") or p.get("symbol")
        mv = float(p.get("market_value") or 0.0)
        if p.get("asset_type") == "OPTION":
            option_mv += mv
            continue
        if und in CASH_LIKE:
            cash_like += mv
            continue
        n = names.setdefault(und, {"symbol": und, "mv": 0.0, "day_pct": p.get("day_profit_loss_pct")})
        n["mv"] += mv
    rows = []
    for n in names.values():
        b = beta.get(n["symbol"])
        n["beta"] = b
        n["pct_book"] = round(100 * n["mv"] / book, 1) if book else None
        n["sector"] = sector.get(n["symbol"], "Unknown")
        rows.append(n)
    rows.sort(key=lambda r: r["mv"], reverse=True)
    invested = sum(r["mv"] for r in rows)
    known = [r for r in rows if r["beta"] is not None]
    beta_dollars = sum(r["mv"] * r["beta"] for r in known)
    unknown_beta = [r["symbol"] for r in rows if r["beta"] is None]

    sec: Dict[str, float] = {}
    for r in rows:
        sec[r["sector"]] = sec.get(r["sector"], 0.0) + r["mv"]
    sectors = [{"sector": k, "pct_book": round(100 * v / book, 1) if book else None}
               for k, v in sorted(sec.items(), key=lambda kv: kv[1], reverse=True)]

    material = [r["symbol"] for r in rows if r["mv"] >= MATERIAL_MV and r["sector"] not in FUND_SECTORS]
    clusters = _clusters(material, corr)

    open_risk, missing_stop, tracked_rows = 0.0, [], []
    for t, rec in tracked.items():
        qty = float(rec.get("quantity") or 0.0)
        entry, stop = rec.get("entry_price"), rec.get("stop")
        long = str(rec.get("side", "LONG")).upper() == "LONG"
        if not stop or not entry:
            missing_stop.append(t)
            continue
        risk = max(0.0, (entry - stop) if long else (stop - entry)) * qty
        open_risk += risk
        tracked_rows.append({"ticker": t, "risk": round(risk), "days_to_earnings": (earnings_days or {}).get(t)})
    risk_pct = round(100 * open_risk / book, 2) if book else None
    untracked = [r["symbol"] for r in rows if r["symbol"] not in tracked and r["mv"] >= MATERIAL_MV and r["sector"] not in FUND_SECTORS]

    dial = regime_dial(regime)
    flags = []
    if risk_pct is not None and risk_pct > MAX_OPEN_RISK_PCT:
        flags.append(f"Open risk {risk_pct}% of book exceeds the {MAX_OPEN_RISK_PCT}% cap")
    if len(tracked) > MAX_POSITIONS:
        flags.append(f"{len(tracked)} tracked positions exceed the {MAX_POSITIONS}-name cap")
    if rows and rows[0]["pct_book"] and rows[0]["pct_book"] >= 25:
        flags.append(f"{rows[0]['symbol']} is {rows[0]['pct_book']}% of the book")
    if clusters:
        flags.append(f"Correlated cluster (rho >= {CORR_LINK}): {', '.join(clusters[0])}")
    for t in tracked_rows:
        d = t.get("days_to_earnings")
        if d is not None and 0 <= d <= 7:
            flags.append(f"{t['ticker']} reports in {d} days")

    beta_pct = round(100 * beta_dollars / book, 1) if book else None
    hedge = None
    if beta_dollars > 0 and book:
        hedge = {
            "beta_dollars": round(beta_dollars),
            "spy_notional_to_neutralize": round(beta_dollars),
            "spy_notional_for_25pct_cut": round(beta_dollars * 0.25),
            "how": "SPY or QQQ puts, or a sector ETF short. Never single-name shorts (measured negative).",
            "measured": False,
            "note": "Arithmetic on beta only. Options legs ($NDX etc.) are not delta-adjusted here.",
        }
    return {
        "book": round(book), "invested_pct": round(100 * invested / book, 1) if book else None,
        "cash_like_pct": round(100 * cash_like / book, 1) if book else None,
        "options_premium": round(option_mv), "options_pct": round(100 * option_mv / book, 1) if book else None,
        "beta_weighted_pct": beta_pct, "unknown_beta": unknown_beta,
        "top": rows[:8], "sectors": sectors, "clusters": clusters,
        "open_risk": round(open_risk), "open_risk_pct": risk_pct, "risk_cap_pct": MAX_OPEN_RISK_PCT,
        "tracked": len(tracked), "position_cap": MAX_POSITIONS, "tracked_rows": tracked_rows,
        "missing_stop": missing_stop, "untracked_holdings": untracked,
        "regime_dial": dial, "hedge": hedge, "flags": flags,
    }


def build_snapshot() -> Dict[str, Any]:
    from src.tracking.position_state import list_open
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions, get_portfolio_summary
    from src.ui.services.action_engine import _days_to_earnings
    from src.ui.services.opportunity_service import get_market_regime

    held = get_portfolio_positions() or []
    summary = get_portfolio_summary() or {}
    tracked = list_open()
    symbols = {(p.get("underlying_symbol") or p.get("symbol")) for p in held if p.get("asset_type") != "OPTION"} | set(tracked)
    stats = refresh_market_stats(symbols)
    earnings = {t: _days_to_earnings(t) for t in tracked}
    snap = compute_snapshot(held, float(summary.get("total_liquidation_value") or 0.0), tracked, stats,
                            get_market_regime(), earnings)
    snap["as_of"] = time.time()
    return snap


def get_snapshot(max_age_s: float = 300.0) -> Dict[str, Any]:
    """Last snapshot immediately; a stale or missing one is rebuilt in the background."""
    global _snapshot, _refreshing
    with _lock:
        fresh = _snapshot and time.time() - _snapshot.get("as_of", 0) < max_age_s
        if not fresh and not _refreshing:
            _refreshing = True

            def _work():
                global _snapshot, _refreshing
                try:
                    snap = build_snapshot()
                    with _lock:
                        _snapshot = snap
                except Exception as e:
                    with _lock:
                        _snapshot = {**_snapshot, "error": str(e), "as_of": time.time()}
                finally:
                    with _lock:
                        _refreshing = False

            threading.Thread(target=_work, name="risk-snapshot", daemon=True).start()
        return dict(_snapshot) if _snapshot else {"loading": True}
