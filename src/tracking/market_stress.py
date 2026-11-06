"""Market stress: daily 0-100 risk score, pre-selloff lead-signal study, VIX>25 event study, punished-quality screen,
and an intraday selling-pressure monitor. Runs inside the nightly universe sweep and the backend tick.

Every rule and threshold below is fixed in code before any outcome is read (pre-registered). The score, the bands and
the intraday triggers are UNMEASURED until `study()` passes its own out-of-sample gate; until then they are context
and never a gate. Daily score history is forward-logged to data/market_stress_ledger.jsonl.

Daily score (components percentile-ranked vs their own trailing 252 sessions, so no hand-tuned levels):
    trend 20, breadth 20 (RSP/SPY proxy), volatility 20 (VIX level, VIX/VIX3M, VIX 5d change),
    credit 15 (HYG/LQD), leadership 10 (SMH/QQQ, QQQ/SPY), rates 5 (10y 20d change).
    Intraday pressure (10) is live only and reported separately.
Bands: <50 NORMAL, 50-65 NO_NEW_ENTRIES, 65-80 REDUCE, >=80 DEFENSIVE. SELECTIVE_ACCUMULATION when the score
peaked >=65 in the last 10 sessions, fell 15+ from that peak and VIX/VIX3M < 1. ADD_EXPOSURE when the score is
back under 50 within 20 sessions of a >=65 reading and QQQ closes above its 20-day average.

python -m src.tracking.market_stress [--study] [--intraday]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import pickle
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
HIST_START = "1990-01-01"
HIST_SYMBOLS = ("^GSPC", "^NDX", "^VIX", "^VIX3M", "^VIX9D", "^VVIX", "^TNX", "SPY", "QQQ", "SMH", "RSP", "HYG", "LQD")
WEIGHTS = {"trend": 20, "breadth": 20, "volatility": 20, "credit": 15, "leadership": 10, "rates": 5}
RANK_WINDOW = 252
BANDS = ((80, "DEFENSIVE"), (65, "REDUCE"), (50, "NO_NEW_ENTRIES"), (0, "NORMAL"))
ACCUM_LOOKBACK, ACCUM_DROP, CONFIRM_LOOKBACK = 10, 15.0, 20
EXPOSURE = {"DEFENSIVE": 0.0, "REDUCE": 0.5}
COST_BPS = 5.0
TRAIN_END = "2015-12-31"
HORIZONS = (5, 10, 20, 60, 120)
VIX_LEVEL, VIX_QUIET_DAYS = 25.0, 20
SELLOFF_1D, SELLOFF_5D, DECLUSTER = -0.04, -0.08, 20
LEAD_WINDOW = 5
STUDY_TTL_DAYS = 7
QUALITY_MIN_DRAWDOWN = 0.20
QUALITY_MAX_FUNDAMENTALS = 60
QUALITY_TOP = 15
QUALITY_QUEUE = 5
QUALITY_TTL_DAYS = 7
QUALITY_WEIGHTS = {"quality": 35, "valuation": 25, "balance": 15, "gap": 15, "timing": 10}
INTRADAY_SYMBOLS = ("QQQ", "SPY", "SMH", "IWM", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC")
SECTOR_ETFS = INTRADAY_SYMBOLS[4:]
INTRADAY_WEIGHTS = {"qqq_below_vwap": 20, "qqq_orb_break": 20, "smh_lagging": 15, "sectors_below_vwap": 25,
                    "qqq_rvol": 10, "vix_up": 10}
INTRADAY_ALERTS = ((80, "CRITICAL"), (60, "WARN"))
INTRADAY_EVERY_S = 300
ORB_MINUTES = 30


def _dir():
    d = config.BASE_DIR / "data" / "market_stress"
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_path():
    return config.BASE_DIR / "data" / "market_stress.json"


def ledger_path():
    return config.BASE_DIR / "data" / "market_stress_ledger.jsonl"


def intraday_log_path():
    return config.BASE_DIR / "data" / "market_stress_intraday.jsonl"


# ---------------------------------------------------------------- history

def fetch_history(session: str) -> Dict[str, Any]:
    """{symbol: DataFrame[date index; open, high, low, close, volume]} from yfinance, cached per session."""
    cache = _dir() / "history.pkl"
    if cache.exists():
        try:
            c = pickle.loads(cache.read_bytes())
            if c.get("session") == session:
                return c["hist"]
        except Exception:
            pass
    import yfinance as yf

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    end = (datetime.fromisoformat(session) + timedelta(days=1)).strftime("%Y-%m-%d")
    raw = yf.download(list(HIST_SYMBOLS), start=HIST_START, end=end, group_by="ticker", auto_adjust=True,
                      progress=False, threads=True, timeout=60)
    hist: Dict[str, Any] = {}
    for s in HIST_SYMBOLS:
        try:
            d = raw[s].dropna(subset=["Close"]).rename(columns=str.lower)
        except KeyError:
            continue
        if len(d):
            d.index = [str(i)[:10] for i in d.index]
            hist[s] = d[[c for c in ("open", "high", "low", "close", "volume") if c in d.columns]]
    if hist:
        cache.write_bytes(pickle.dumps({"session": session, "hist": hist}))
    return hist


def _closes(hist: Dict[str, Any]):
    import pandas as pd

    return pd.DataFrame({s: d["close"] for s, d in hist.items()}).sort_index().ffill()


# ---------------------------------------------------------------- daily score

def _prank(x, window: int = RANK_WINDOW):
    """Trailing percentile rank in [0, 1] of each value vs its own last `window` values."""
    return x.rolling(window, min_periods=window // 2).apply(lambda w: (w[:-1] < w[-1]).mean() if len(w) > 1 else math.nan,
                                                          raw=True)


def components(px) -> Any:
    """Stress components in [0, 1] (1 = most stressed), one row per date. NaN where the inputs did not exist yet."""
    import pandas as pd

    c = pd.DataFrame(index=px.index)
    col = lambda s: px[s] if s in px else pd.Series(math.nan, index=px.index)
    q = col("QQQ").fillna(col("^NDX"))
    below50 = (q < q.rolling(50).mean()).astype(float)
    below200 = (q < q.rolling(200).mean()).astype(float)
    c["trend"] = (below50 + below200 + _prank(-q.pct_change(20))) / 3
    c["breadth"] = _prank(-(col("RSP") / col("SPY")).pct_change(20))
    vix = col("^VIX")
    term = (vix / col("^VIX3M")).clip(upper=2.0)
    c["volatility"] = pd.concat([_prank(vix), (term > 1).astype(float).where(term.notna()), _prank(vix.pct_change(5))],
                                axis=1).mean(axis=1, skipna=True)
    c["credit"] = _prank(-(col("HYG") / col("LQD")).pct_change(20))
    c["leadership"] = pd.concat([_prank(-(col("SMH") / q).pct_change(20)), _prank(-(q / col("SPY")).pct_change(20))],
                                axis=1).mean(axis=1)
    c["rates"] = _prank(col("^TNX").diff(20).abs())
    return c


def score_series(comp) -> Any:
    """0-100 weighted mean over the components available on each date (weights renormalised)."""
    import pandas as pd

    w = pd.Series(WEIGHTS)
    avail = comp[list(w.index)].notna()
    num = comp[list(w.index)].fillna(0).mul(w, axis=1).sum(axis=1)
    den = avail.mul(w, axis=1).sum(axis=1)
    return (100 * num / den).where(den >= 50)


def band_of(score: float) -> str:
    return next(name for edge, name in BANDS if score >= edge)


def actions(score, term, qqq) -> Any:
    """Band per date plus the two recovery states, from the score path, VIX/VIX3M and QQQ vs its 20-day average."""
    import pandas as pd

    out = []
    s = score.tolist()
    t = term.reindex(score.index).tolist()
    above20 = (qqq > qqq.rolling(20).mean()).reindex(score.index).tolist()
    for i, v in enumerate(s):
        if v is None or v != v:
            out.append(None)
            continue
        recent = [x for x in s[max(0, i - ACCUM_LOOKBACK):i + 1] if x == x]
        older = [x for x in s[max(0, i - CONFIRM_LOOKBACK):i + 1] if x == x]
        peak = max(recent) if recent else v
        if v < 50 and older and max(older) >= 65 and above20[i]:
            out.append("ADD_EXPOSURE")
        elif peak >= 65 and peak - v >= ACCUM_DROP and t[i] is not None and t[i] == t[i] and t[i] < 1:
            out.append("SELECTIVE_ACCUMULATION")
        else:
            out.append(band_of(v))
    return pd.Series(out, index=score.index)


# ---------------------------------------------------------------- studies

def _events(mask, gap: int) -> List[int]:
    """Indexes where mask turns true, at least `gap` sessions after the previous kept event."""
    out: List[int] = []
    for i, m in enumerate(mask):
        if m and (not out or i - out[-1] >= gap):
            out.append(i)
    return out


def _fwd_stats(closes: List[float], idx: List[int]) -> Dict[str, Any]:
    res: Dict[str, Any] = {"n": len(idx)}
    for h in HORIZONS:
        r = sorted(closes[i + h] / closes[i] - 1 for i in idx if i + h < len(closes))
        if not r:
            continue
        pct = lambda p: round(100 * r[min(len(r) - 1, int(p * len(r)))], 2)
        res[f"{h}d"] = {"n": len(r), "mean": round(100 * sum(r) / len(r), 2), "median": pct(0.5), "p10": pct(0.1),
                        "p90": pct(0.9), "win_pct": round(100 * sum(x > 0 for x in r) / len(r), 1)}
    for dd in (0.05, 0.10):
        hit = [i for i in idx if i + 60 < len(closes) and min(closes[i + 1:i + 61]) <= closes[i] * (1 - dd)]
        base = [i for i in idx if i + 60 < len(closes)]
        if base:
            res[f"p_further_{int(dd * 100)}pct_60d"] = round(100 * len(hit) / len(base), 1)
    return res


def vix_study(px) -> Dict[str, Any]:
    """First close above 25 after 20+ sessions at or below it; forward index returns and stabilization variants."""
    vix = px["^VIX"].dropna()
    spx = px["^GSPC"].reindex(vix.index).ffill()
    ndx = px["^NDX"].reindex(vix.index).ffill()
    term = (vix / px["^VIX3M"].reindex(vix.index)) if "^VIX3M" in px else None
    v = vix.tolist()
    quiet = [i >= VIX_QUIET_DAYS and v[i] > VIX_LEVEL and max(v[i - VIX_QUIET_DAYS:i]) <= VIX_LEVEL for i in range(len(v))]
    ev = [i for i, q in enumerate(quiet) if q]
    sc, nc = spx.tolist(), ndx.tolist()
    sma200 = spx.rolling(200).mean().tolist()
    dates = list(vix.index)
    era = lambda i: "1990-2007" if dates[i] < "2008" else ("2008-2016" if dates[i] < "2017" else "2017-2026")
    out: Dict[str, Any] = {"definition": f"first VIX close > {VIX_LEVEL:g} after {VIX_QUIET_DAYS}+ sessions <= {VIX_LEVEL:g}",
                           "events": [dates[i] for i in ev],
                           "baseline_spx": _fwd_stats(sc, list(range(0, len(sc) - 1, 5))),
                           "spx": _fwd_stats(sc, ev), "ndx": _fwd_stats(nc, [i for i in ev if nc[i] == nc[i]]),
                           "by_era": {e: _fwd_stats(sc, [i for i in ev if era(i) == e]) for e in ("1990-2007", "2008-2016", "2017-2026")},
                           "spx_above_200d": _fwd_stats(sc, [i for i in ev if sma200[i] == sma200[i] and sc[i] > sma200[i]]),
                           "spx_below_200d": _fwd_stats(sc, [i for i in ev if sma200[i] == sma200[i] and sc[i] <= sma200[i]])}
    t = term.tolist() if term is not None else None
    if t is not None:
        out["backwardation"] = _fwd_stats(sc, [i for i in ev if t[i] == t[i] and t[i] > 1])
        out["contango"] = _fwd_stats(sc, [i for i in ev if t[i] == t[i] and t[i] <= 1])
    variants = {"vix_back_below_25": lambda i: v[i] < VIX_LEVEL,
                "spx_close_above_prior_3d_high": lambda i: sc[i] > max(sc[i - 3:i])}
    if t is not None:
        variants["term_back_below_1"] = lambda i: t[i] == t[i] and t[i] < 1
    for name, cond in variants.items():
        entries = []
        for e in ev:
            j = next((k for k in range(e + 1, min(e + 61, len(sc))) if cond(k)), None)
            if j is not None:
                entries.append(j)
        out[f"wait_{name}"] = _fwd_stats(sc, entries)
    out["note"] = ("Descriptive event study; overlapping horizons and few independent events. "
                   "Not a gate. 'wait_*' enters at the first session meeting the condition within 60 sessions.")
    return out


LEAD_FLAGS = {
    "vix_term_backwardation": lambda px: px["^VIX"] / px["^VIX3M"] > 1,
    "vix_5d_up_20pct": lambda px: px["^VIX"].pct_change(5) > 0.20,
    "rsp_spy_20d_bottom_decile": lambda px: _prank((px["RSP"] / px["SPY"]).pct_change(20)) < 0.10,
    "smh_qqq_5d_down_3pct": lambda px: (px["SMH"] / px["QQQ"]).pct_change(5) < -0.03,
    "hyg_lqd_20d_bottom_decile": lambda px: _prank((px["HYG"] / px["LQD"]).pct_change(20)) < 0.10,
    "qqq_below_50d": lambda px: px["QQQ"] < px["QQQ"].rolling(50).mean(),
    "tnx_5d_up_20bp": lambda px: px["^TNX"].diff(5) > 0.20,
    "vvix_above_110": lambda px: px["^VVIX"] > 110,
    "score_ge_65": None,
}


def lead_study(px, score) -> Dict[str, Any]:
    """For each pre-registered flag: P(flag fired in the 5 sessions before a QQQ selloff) vs P(fired in any 5 sessions)."""
    q = px["QQQ"].dropna()
    r1, r5 = q.pct_change(1), q.pct_change(5)
    idx = _events(((r1 <= SELLOFF_1D) | (r5 <= SELLOFF_5D)).tolist(), DECLUSTER)
    dates = list(q.index)
    out: Dict[str, Any] = {"definition": f"QQQ 1d <= {SELLOFF_1D:.0%} or 5d <= {SELLOFF_5D:.0%}, de-clustered {DECLUSTER} sessions",
                           "events": [dates[i] for i in idx], "flags": {}}
    for name, fn in LEAD_FLAGS.items():
        try:
            f = (score >= 65) if fn is None else fn(px)
            f = f.reindex(q.index).fillna(False).astype(bool)
        except KeyError:
            continue
        prior = f.astype(float).rolling(LEAD_WINDOW).max().shift(1).fillna(0).astype(bool).tolist()
        valid = [i for i in idx if i >= LEAD_WINDOW]
        hits = sum(prior[i] for i in valid)
        base = sum(prior) / max(1, len(prior))
        train = [i for i in valid if dates[i] <= TRAIN_END]
        test = [i for i in valid if dates[i] > TRAIN_END]
        rate = lambda xs: round(100 * sum(prior[i] for i in xs) / len(xs), 1) if xs else None
        out["flags"][name] = {"hit_pct": round(100 * hits / len(valid), 1) if valid else None,
                              "base_pct": round(100 * base, 1),
                              "lift": round((hits / len(valid)) / base, 2) if valid and base else None,
                              "train_hit_pct": rate(train), "test_hit_pct": rate(test), "n_events": len(valid),
                              "fired_last_5d": bool(f.tail(LEAD_WINDOW).any())}
    out["note"] = "Lift > 1 in BOTH train (<=2015) and test (>2015) is the minimum bar; a flag that fires constantly has no lift."
    return out


def _perf(rets: List[float]) -> Dict[str, Any]:
    if not rets:
        return {}
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    yrs = len(rets) / 252
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)) ** 0.5
    return {"cagr_pct": round(100 * (eq ** (1 / yrs) - 1), 2) if yrs > 0 and eq > 0 else None,
            "max_dd_pct": round(100 * mdd, 1), "sharpe": round(mu / sd * 252 ** 0.5, 2) if sd else None}


def backtest(px, act) -> Dict[str, Any]:
    """QQQ held at the band's exposure from the next session, 5 bps per unit of exposure change, vs buy and hold.
    Also counts alarms (first REDUCE/DEFENSIVE after 20 sessions without) and >=10% QQQ drawdowns."""
    q = px["QQQ"].reindex(act.index).ffill()
    act, q = act[q.notna()], q[q.notna()]
    rets = q.pct_change().fillna(0).tolist()
    a = act.tolist()
    expo = [EXPOSURE.get(x, 1.0) if x else 1.0 for x in a]
    dates = list(act.index)
    strat, bh, prev = [], [], 1.0
    for i in range(1, len(rets)):
        e = expo[i - 1]
        strat.append(e * rets[i] - abs(e - prev) * COST_BPS / 1e4)
        bh.append(rets[i])
        prev = e
    split = next((i for i, d in enumerate(dates[1:]) if d > TRAIN_END), len(strat))
    alarm = [x in ("REDUCE", "DEFENSIVE") for x in a]
    alarms = _events(alarm, 20)
    qc = q.tolist()
    false = [i for i in alarms if i + 20 < len(qc) and min(qc[i + 1:i + 21]) > qc[i] * 0.95]
    peak_i, drawdowns, i = 0, [], 0
    while i < len(qc):
        if qc[i] >= qc[peak_i]:
            peak_i = i
        elif qc[i] <= qc[peak_i] * 0.90:
            drawdowns.append((peak_i, i))
            while i < len(qc) and qc[i] < qc[peak_i]:
                i += 1
            peak_i = min(i, len(qc) - 1)
            continue
        i += 1
    warned = [d for d in drawdowns if any(d[0] <= k <= d[1] and qc[k] >= qc[d[0]] * 0.95 for k in alarms)]
    res = {"train": {"strategy": _perf(strat[:split]), "buy_hold": _perf(bh[:split])},
           "test": {"strategy": _perf(strat[split:]), "buy_hold": _perf(bh[split:])},
           "alarms": len(alarms), "false_alarms": len(false), "drawdowns_10pct": len(drawdowns),
           "drawdowns_warned_before_minus5": len(warned),
           "missed": [dates[d[0]] for d in drawdowns if d not in warned],
           "cost_bps_per_side": COST_BPS, "train_end": TRAIN_END}
    def better(p: Dict[str, Any]) -> bool:
        s, b = p["strategy"], p["buy_hold"]
        if None in (s.get("sharpe"), b.get("sharpe"), s.get("max_dd_pct"), b.get("max_dd_pct")):
            return False
        return s["sharpe"] > b["sharpe"] and s["max_dd_pct"] > b["max_dd_pct"]

    res["verdict"] = "MEASURED" if better(res["train"]) and better(res["test"]) else "UNMEASURED"
    return res


def study(session: str, force: bool = False) -> Dict[str, Any]:
    """Weekly: history, score path, lead-signal study, VIX>25 study, backtest. Cached in data/market_stress/study.json."""
    p = _dir() / "study.json"
    if not force and p.exists():
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            if (datetime.fromisoformat(session) - datetime.fromisoformat(s["session"])).days < STUDY_TTL_DAYS:
                return s
        except Exception:
            pass
    hist = fetch_history(session)
    px = _closes(hist)
    comp = components(px)
    score = score_series(comp)
    term = px["^VIX"] / px["^VIX3M"] if "^VIX3M" in px else score * math.nan
    act = actions(score, term, px["QQQ"])
    s = {"session": session, "built_at": datetime.now().isoformat(timespec="seconds"),
         "vix": vix_study(px), "lead": lead_study(px, score), "backtest": backtest(px, act.dropna())}
    p.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    return s


# ---------------------------------------------------------------- punished quality

def drawdown_candidates(bars: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out = []
    for t, b in bars.items():
        if len(b) < 252 or t in INTRADAY_SYMBOLS:
            continue
        c = [float(x["close"]) for x in b]
        hi = max(float(x["high"]) for x in b[-252:])
        dd, r21 = 1 - c[-1] / hi, c[-1] / c[-22] - 1
        if dd >= QUALITY_MIN_DRAWDOWN or r21 <= -0.10:
            out.append({"ticker": t, "close": round(c[-1], 2), "drawdown_pct": round(100 * dd, 1),
                        "r21_pct": round(100 * r21, 1), "high_252d": round(hi, 2)})
    return sorted(out, key=lambda r: -r["drawdown_pct"])


def fetch_fundamentals(tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    """yfinance info fields per ticker, cached QUALITY_TTL_DAYS. Absent fields stay absent."""
    p = _dir() / "fundamentals.json"
    try:
        cache = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    now = time.time()
    keys = ("freeCashflow", "totalRevenue", "grossMargins", "operatingMargins", "profitMargins", "returnOnEquity",
            "returnOnAssets", "totalDebt", "totalCash", "ebitda", "forwardPE", "trailingPE", "revenueGrowth",
            "earningsGrowth", "marketCap", "enterpriseValue", "sector", "shortName", "earningsTimestampStart")
    todo = [t for t in tickers if now - (cache.get(t) or {}).get("_ts", 0) > QUALITY_TTL_DAYS * 86400]
    if todo:
        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        for t in todo:
            try:
                info = yf.Ticker(t.replace(".", "-")).info or {}
                cache[t] = {k: info.get(k) for k in keys if info.get(k) is not None} | {"_ts": now}
            except Exception as e:
                logger.debug("fundamentals %s failed: %s", t, e)
        p.write_text(json.dumps(cache), encoding="utf-8")
    return {t: cache.get(t) or {} for t in tickers}


def _clip01(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    return None if x is None else max(0.0, min(1.0, (x - lo) / (hi - lo)))


def quality_row(cand: Dict[str, Any], f: Dict[str, Any], today: datetime) -> Dict[str, Any]:
    """Weighted 0-100 score from fundamentals; value-trap flags listed separately. Weights are declared, unmeasured."""
    fcf, rev, ev = f.get("freeCashflow"), f.get("totalRevenue"), f.get("enterpriseValue")
    debt, cash, ebitda = f.get("totalDebt") or 0, f.get("totalCash") or 0, f.get("ebitda")
    fcf_margin = fcf / rev if fcf and rev else None
    ev_fcf = ev / fcf if ev and fcf and fcf > 0 else None
    nd_ebitda = (debt - cash) / ebitda if ebitda and ebitda > 0 else None
    mean = lambda xs: sum(x for x in xs if x is not None) / max(1, sum(x is not None for x in xs)) \
        if any(x is not None for x in xs) else None
    parts = {
        "quality": mean([_clip01(fcf_margin, 0.0, 0.30), _clip01(f.get("operatingMargins"), 0.0, 0.35),
                         _clip01(f.get("returnOnEquity"), 0.0, 0.30), _clip01(f.get("revenueGrowth"), -0.05, 0.25)]),
        "valuation": mean([None if not f.get("forwardPE") or f["forwardPE"] <= 0 else 1 - _clip01(f["forwardPE"], 10, 45),
                           None if ev_fcf is None else 1 - _clip01(ev_fcf, 10, 60)]),
        "balance": None if nd_ebitda is None else 1 - _clip01(nd_ebitda, -1.0, 4.0),
        "gap": _clip01(cand["drawdown_pct"], 10, 50),
    }
    e_ts = f.get("earningsTimestampStart")
    e_days = (datetime.fromtimestamp(e_ts) - today).days if e_ts else None
    parts["timing"] = 0.0 if e_days is not None and 0 <= e_days <= 14 else 1.0
    traps = [x for x, bad in (("no_fcf", fcf is not None and fcf <= 0), ("revenue_shrinking", (f.get("revenueGrowth") or 0) < 0),
                              ("earnings_falling_20pct", (f.get("earningsGrowth") or 0) < -0.20),
                              ("net_debt_over_3x_ebitda", nd_ebitda is not None and nd_ebitda > 3),
                              ("earnings_within_14d", e_days is not None and 0 <= e_days <= 14)) if bad]
    have = {k: v for k, v in parts.items() if v is not None}
    w = sum(QUALITY_WEIGHTS[k] for k in have)
    score = round(100 * sum(QUALITY_WEIGHTS[k] * v for k, v in have.items()) / w, 1) if w >= 60 else None
    return {**cand, "name": f.get("shortName"), "sector": f.get("sector"), "score": score,
            "parts": {k: (round(v, 2) if v is not None else None) for k, v in parts.items()},
            "fcf_margin_pct": round(100 * fcf_margin, 1) if fcf_margin is not None else None,
            "forward_pe": round(f["forwardPE"], 1) if f.get("forwardPE") else None,
            "ev_fcf": round(ev_fcf, 1) if ev_fcf else None,
            "net_debt_ebitda": round(nd_ebitda, 2) if nd_ebitda is not None else None,
            "revenue_growth_pct": round(100 * f["revenueGrowth"], 1) if f.get("revenueGrowth") is not None else None,
            "earnings_in_days": e_days, "value_trap_flags": traps}


def punished_quality(bars: Dict[str, List[Dict[str, Any]]], fetch=fetch_fundamentals) -> List[Dict[str, Any]]:
    cands = drawdown_candidates(bars)[:QUALITY_MAX_FUNDAMENTALS]
    funds = fetch([c["ticker"] for c in cands]) if cands else {}
    today = datetime.now()
    rows = [quality_row(c, funds.get(c["ticker"]) or {}, today) for c in cands]
    rows = [r for r in rows if r["score"] is not None]
    return sorted(rows, key=lambda r: (len(r["value_trap_flags"]) > 0, -r["score"]))[:QUALITY_TOP]


# ---------------------------------------------------------------- nightly run

def _append_ledger(row: Dict[str, Any]) -> None:
    p = ledger_path()
    try:
        seen = {json.loads(x).get("session") for x in p.read_text(encoding="utf-8").splitlines() if x.strip()}
    except Exception:
        seen = set()
    if row["session"] not in seen:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


def _notify(prev: Optional[Dict[str, Any]], cur: Dict[str, Any]) -> None:
    if prev and prev.get("action") == cur["action"]:
        return
    sev = {"DEFENSIVE": "CRITICAL", "REDUCE": "WARN", "NO_NEW_ENTRIES": "WARN"}.get(cur["action"], "INFO")
    try:
        from src.tracking import notification_center as nc

        nc.add("market_stress", sev, f"Market stress {cur['action']} (score {cur['score']:.0f})",
               f"Session {cur['session']}. Components: " + ", ".join(f"{k} {v:.2f}" for k, v in cur["components"].items())
               + f". Was {prev.get('action') if prev else 'n/a'}. Label: {cur['label']}.",
               dedupe_key=f"market_stress:{cur['session']}:{cur['action']}")
    except Exception as e:
        logger.warning("market stress notify failed: %s", e)


def _queue_quality(rows: List[Dict[str, Any]], action: str) -> List[str]:
    if action not in ("REDUCE", "DEFENSIVE", "SELECTIVE_ACCUMULATION"):
        return []
    from src.tracking.alert_db import queue_for_research

    queued = []
    for r in [x for x in rows if not x["value_trap_flags"]][:QUALITY_QUEUE]:
        try:
            if queue_for_research(r["ticker"], source="market_stress", setup="Punished quality",
                                  reason=f"stress {action}: quality {r['score']}, {r['drawdown_pct']}% off 52w high"):
                queued.append(r["ticker"])
        except Exception as e:
            logger.warning("stress queue %s failed: %s", r["ticker"], e)
    return queued


def run(bars: Dict[str, List[Dict[str, Any]]], market: Optional[Dict[str, Any]] = None, session: Optional[str] = None,
        queue: bool = True) -> Dict[str, Any]:
    """Nightly, from the universe sweep. Writes data/market_stress.json and appends the forward ledger."""
    if session is None:
        from src.tracking.backend_tasks import current_session

        session = current_session()
    try:
        prev = json.loads(out_path().read_text(encoding="utf-8"))
    except Exception:
        prev = None
    hist = fetch_history(session)
    px = _closes(hist)
    comp = components(px)
    score = score_series(comp)
    term = px["^VIX"] / px["^VIX3M"] if "^VIX3M" in px else score * math.nan
    act = actions(score, term, px["QQQ"])
    last = score.dropna().index[-1]
    st = study(session)
    label = "MEASURED" if (st.get("backtest") or {}).get("verdict") == "MEASURED" else "UNMEASURED"
    vix_now = float(px["^VIX"].dropna().iloc[-1]) if "^VIX" in px else None
    quality = punished_quality(bars) if bars else []
    cur = {"session": session, "as_of_bar": last, "built_at": datetime.now().isoformat(timespec="seconds"),
           "score": round(float(score[last]), 1), "action": act[last], "label": label,
           "components": {k: round(float(comp.loc[last, k]), 3) for k in WEIGHTS if comp.loc[last, k] == comp.loc[last, k]},
           "score_10d": [round(float(x), 1) for x in score.dropna().tail(10)],
           "vix": round(vix_now, 2) if vix_now else None,
           "vix_term": round(float(term.dropna().iloc[-1]), 3) if term.notna().any() else None,
           "breadth_live": {k: (market or {}).get(k) for k in ("advancing_pct", "above_50d_pct", "above_200d_pct",
                                                              "new_highs", "new_lows")},
           "lead_flags_last_5d": {k: v["fired_last_5d"] for k, v in (st.get("lead") or {}).get("flags", {}).items()},
           "quality": quality,
           "note": "Score, bands and quality weights are pre-registered and UNMEASURED unless label says MEASURED. "
                   "Context only; the engine zone and at-market R:R decide entries."}
    cur["queued"] = _queue_quality(quality, cur["action"]) if queue else []
    out_path().write_text(json.dumps(cur, indent=1, default=str), encoding="utf-8")
    _append_ledger({k: cur[k] for k in ("session", "as_of_bar", "score", "action", "components", "vix", "vix_term")})
    _notify(prev, cur)
    return cur


def latest() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, p in (("daily", out_path()), ("study", _dir() / "study.json")):
        try:
            out[key] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            out[key] = None
    try:
        lines = intraday_log_path().read_text(encoding="utf-8").splitlines()
        out["intraday"] = json.loads(lines[-1]) if lines else None
    except Exception:
        out["intraday"] = None
    return out


# ---------------------------------------------------------------- intraday

def _vwap_state(candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Today's RTH 5m candles -> last close, VWAP, failed reclaims, opening-range low and cumulative volume."""
    day = [c for c in candles if c.get("session") == "RTH"]
    if not day:
        return None
    today = day[-1]["date"]
    day = [c for c in day if c["date"] == today]
    pv = vol = 0.0
    fails = 0
    vw = None
    for c in day:
        tp = (c["high"] + c["low"] + c["close"]) / 3
        pv += tp * c["volume"]
        vol += c["volume"]
        vw = pv / vol if vol else c["close"]
        if c["high"] > vw > c["close"]:
            fails += 1
    orb = day[:ORB_MINUTES // 5]
    return {"date": today, "open": day[0]["open"], "close": day[-1]["close"], "vwap": vw, "failed_reclaims": fails,
            "orb_low": min(c["low"] for c in orb), "orb_done": len(day) > len(orb), "bars": len(day), "volume": vol}


def _prior_volume(candles: List[Dict[str, Any]], today: str, bars: int) -> Optional[float]:
    prior = [c for c in candles if c.get("session") == "RTH" and c["date"] < today]
    if not prior:
        return None
    last = prior[-1]["date"]
    day = [c for c in prior if c["date"] == last][:bars]
    return sum(c["volume"] for c in day) or None


def intraday_pressure(candles: Dict[str, List[Dict[str, Any]]], vix_change: Optional[float] = None) -> Dict[str, Any]:
    """0-100 selling pressure from pre-registered triggers. Pure: takes normalized 5m candles per symbol."""
    st = {s: _vwap_state(c) for s, c in candles.items()}
    q, smh = st.get("QQQ"), st.get("SMH")
    sig: Dict[str, Optional[bool]] = {k: None for k in INTRADAY_WEIGHTS}
    if q:
        sig["qqq_below_vwap"] = q["close"] < q["vwap"] and q["failed_reclaims"] >= 2
        sig["qqq_orb_break"] = q["orb_done"] and q["close"] < q["orb_low"]
        pv = _prior_volume(candles["QQQ"], q["date"], q["bars"])
        sig["qqq_rvol"] = (q["volume"] / pv >= 1.5) if pv else None
        if smh:
            sig["smh_lagging"] = (smh["close"] / smh["open"]) - (q["close"] / q["open"]) <= -0.01
    secs = [st[s] for s in SECTOR_ETFS if st.get(s)]
    below = sum(1 for s in secs if s["close"] < s["vwap"])
    if len(secs) >= 6:
        sig["sectors_below_vwap"] = below / len(secs) >= 0.8
    if vix_change is not None:
        sig["vix_up"] = vix_change >= 0.10
    have = {k: v for k, v in sig.items() if v is not None}
    w = sum(INTRADAY_WEIGHTS[k] for k in have)
    pressure = round(100 * sum(INTRADAY_WEIGHTS[k] for k, v in have.items() if v) / w, 1) if w >= 50 else None
    return {"pressure": pressure, "signals": sig, "sectors_below_vwap": f"{below}/{len(secs)}",
            "qqq": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in (q or {}).items()},
            "invalidation": "QQQ closes two 5m bars back above session VWAP",
            "label": "UNMEASURED"}


def intraday_check(now_et: Optional[datetime] = None) -> Dict[str, Any]:
    """Live: fetch 5m candles, score pressure, log every check, alert once per level per day."""
    from src.clients.schwab_client import get_intraday_candles
    from src.tracking.candle_service import _normalize_intraday

    now_et = now_et or datetime.now(ET)
    candles = {s: _normalize_intraday(get_intraday_candles(s, frequency=5, lookback_bars=288)) for s in INTRADAY_SYMBOLS}
    q = _vwap_state(candles.get("QQQ") or [])
    if not q or q["date"] != now_et.date().isoformat():
        return {"ts": now_et.isoformat(timespec="seconds"), "skipped": "no RTH candles today"}
    vix_change = None
    try:
        from src.clients.price_client import get_current_price
        from src.tracking.backend_tasks import current_session

        hist = fetch_history(current_session(now_et))
        prev = float(hist["^VIX"]["close"].iloc[-1]) if "^VIX" in hist else None
        live = get_current_price("^VIX")
        vix_change = live / prev - 1 if live and prev else None
    except Exception as e:
        logger.debug("intraday VIX change unavailable: %s", e)
    res = {"ts": now_et.isoformat(timespec="seconds"), **intraday_pressure(candles, vix_change)}
    with intraday_log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(res, default=str) + "\n")
    p = res["pressure"]
    lvl = next((sev for edge, sev in INTRADAY_ALERTS if p is not None and p >= edge), None)
    if lvl:
        try:
            from src.tracking import notification_center as nc

            fired = [k for k, v in res["signals"].items() if v]
            nc.add("intraday_stress", lvl, f"Intraday selling pressure {p:.0f}/100",
                   f"Triggers: {', '.join(fired)}. Sectors below VWAP {res['sectors_below_vwap']}. "
                   f"Invalidation: {res['invalidation']}. Label UNMEASURED.",
                   dedupe_key=f"intraday_stress:{now_et.date()}:{lvl}")
        except Exception as e:
            logger.warning("intraday stress notify failed: %s", e)
    return res


def intraday_due(now_et: datetime, last_ts: Optional[float]) -> bool:
    rth = now_et.weekday() < 5 and (9, 30 + ORB_MINUTES // 2) <= (now_et.hour, now_et.minute) < (16, 0)
    return rth and (not last_ts or now_et.timestamp() - float(last_ts) >= INTRADAY_EVERY_S)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", action="store_true", help="rebuild the weekly study now")
    ap.add_argument("--intraday", action="store_true", help="run one intraday check")
    a = ap.parse_args()
    from src.tracking.backend_tasks import current_session

    if a.intraday:
        print(json.dumps(intraday_check(), indent=1, default=str))
    elif a.study:
        print(json.dumps(study(current_session(), force=True)["backtest"], indent=1))
    else:
        print(json.dumps(run({}, queue=False), indent=1, default=str)[:4000])
