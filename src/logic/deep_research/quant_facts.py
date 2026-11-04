"""quant_facts.py — code-computed base rates and risk statistics for the research report.

Daily bars come from yfinance (10 years, cached once per day under ``data/quant_cache``);
the Data Window CSV (~412 bars) is the fallback. SPY and the sector ETF are cached the same way.

Every rate prints its n beside it. A rate with n < MIN_N is "insufficient history", a missing
input is "unavailable" — never 0. Empirical touch/base rates are labelled base rates; the
driftless-GBM sigma model (scenario_plans.touch_odds) is labelled a model estimate, never a
probability of outcome.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import statistics
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HORIZONS = (5, 21, 63, 126, 252)
HORIZON_KEYS = ("1W", "1M", "3M", "6M", "1Y")
KEY_TO_BARS = dict(zip(HORIZON_KEYS, HORIZONS))
MIN_N = 30


# ---------------------------------------------------------------------------
# Daily bars (yfinance, cached once per day; DW CSV fallback)
# ---------------------------------------------------------------------------

def _cache_dir(raw_dir: Optional[Path]) -> Path:
    from src import config

    d = Path(raw_dir) / "_quant" if raw_dir else config.BASE_DIR / "data" / "quant_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_csv_bars(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    o, h, lo, c = (float(row[k]) for k in ("open", "high", "low", "close"))
                    v = float(row.get("volume") or 0)
                except (KeyError, TypeError, ValueError):
                    continue
                out.append({"time": str(row.get("time") or row.get("date") or "")[:10],
                            "open": o, "high": h, "low": lo, "close": c, "volume": v})
    except Exception:
        return []
    return out


def _yf_download(symbol: str, period: str = "10y") -> List[Dict[str, Any]]:
    import pandas as pd
    import yfinance as yf

    hist = yf.download(symbol, period=period, auto_adjust=True, progress=False)
    if hist is None or hist.empty:
        return []
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.droplevel(-1)
    out: List[Dict[str, Any]] = []
    for idx, r in hist.iterrows():
        try:
            c = float(r["Close"])
            if not c or c != c:
                continue
            out.append({"time": str(idx)[:10],
                        "open": float(r["Open"]), "high": float(r["High"]),
                        "low": float(r["Low"]), "close": c,
                        "volume": float(r.get("Volume") or 0)})
        except Exception:
            continue
    return out


def bars_settled(now: Optional[datetime] = None) -> bool:
    """True once today's daily bar is final: past the configured close, or a non-trading day."""
    from src import config
    from src.utils.market_calendar import is_trading_day

    now = now or datetime.now()
    return (not is_trading_day(now.date())
            or (now.hour, now.minute) >= (config.MARKET_CLOSE_HOUR, config.MARKET_CLOSE_MINUTE))


def daily_bars(symbol: str, cache_dir: Path) -> Tuple[List[Dict[str, Any]], str]:
    """Up to 10y of daily OHLCV for *symbol*, cached in *cache_dir* once per calendar day.
    A cache written before the close is refetched once the bar settles."""
    safe = symbol.replace(":", "_").replace("^", "")
    path = Path(cache_dir) / f"{safe}_daily.csv"
    today = date.today().isoformat()
    settled = bars_settled()
    if path.exists():
        try:
            meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
            if meta.get("date") == today and (meta.get("settled") or not settled):
                bars = _parse_csv_bars(path)
                if bars:
                    return bars, "yfinance cache"
        except Exception:
            pass
    try:
        bars = _yf_download(symbol)
    except Exception as e:
        logger.debug(f"{symbol} yfinance download failed: {e}")
        bars = []
    if bars:
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=["time", "open", "high", "low", "close", "volume"])
                w.writeheader()
                w.writerows(bars)
            path.with_suffix(".meta.json").write_text(json.dumps({"date": today, "n": len(bars), "settled": settled}), encoding="utf-8")
        except Exception as e:
            logger.debug(f"{symbol} bar cache write failed: {e}")
        return bars, "yfinance"
    if path.exists():
        bars = _parse_csv_bars(path)
        if bars:
            return bars, "yfinance cache (stale)"
    return [], "unavailable"


# ---------------------------------------------------------------------------
# Empirical statistics (pure math on bar lists — testable without IO)
# ---------------------------------------------------------------------------

def forward_returns(closes: List[float], h: int) -> Dict[str, Any]:
    """p10/p50/p90 of the h-bar forward simple return over every start with h bars ahead."""
    rets = [closes[i + h] / closes[i] - 1.0 for i in range(len(closes) - h) if closes[i] > 0]
    n = len(rets)
    if n < MIN_N:
        return {"n": n}
    rets.sort()
    q = statistics.quantiles(rets, n=10)
    return {"p10": round(q[0] * 100, 2), "p50": round(statistics.median(rets) * 100, 2),
            "p90": round(q[8] * 100, 2), "n": n}


def _future_extreme(series: List[float], h: int, fn: str) -> List[Optional[float]]:
    """max/min over the next h values AFTER each index; None where the window is short."""
    import pandas as pd

    rolled = getattr(pd.Series(series, dtype="float64").rolling(h), fn)()
    return rolled.shift(-h).tolist()


def touch_prob(highs: List[float], lows: List[float], closes: List[float],
               level: float, h: int,
               _extremes: Optional[Tuple[List[Optional[float]], List[Optional[float]]]] = None) -> Dict[str, Any]:
    """P(price touches *level* within the next h bars). The level is measured as a % distance from
    the latest close and re-applied to every historical close, so old price eras stay comparable."""
    if not closes or closes[-1] <= 0 or level == closes[-1]:
        return {"n": 0}
    pct = level / closes[-1] - 1.0
    fmax, fmin = _extremes or (_future_extreme(highs, h, "max"), _future_extreme(lows, h, "min"))
    hits = 0
    total = 0
    for i in range(len(closes)):
        fm, fn = fmax[i], fmin[i]
        if fm is None or fn is None or math.isnan(fm) or math.isnan(fn) or closes[i] <= 0:
            continue
        total += 1
        tgt = closes[i] * (1.0 + pct)
        hits += 1 if (fm >= tgt if pct > 0 else fn <= tgt) else 0
    if total < MIN_N:
        return {"n": total}
    return {"pct": round(100 * hits / total, 1), "n": total}


def first_touch_race(highs: List[float], lows: List[float], closes: List[float],
                     levels: Dict[str, float], h: int) -> Dict[str, Any]:
    """Among *levels*, which is touched first within h bars, and how often none is.

    Each level is a % distance from the latest close, re-applied to every historical close
    (above -> high touch, below -> low touch). Returns {"n", "first": {name: share%}, "none_pct": pct}.
    """
    n = len(closes)
    if n < h + MIN_N or not levels or closes[-1] <= 0:
        return {"n": 0}
    pcts = {k: lv / closes[-1] - 1.0 for k, lv in levels.items() if lv != closes[-1]}
    first = {k: 0 for k in levels}
    none = 0
    total = 0
    for i in range(n - h):
        if closes[i] <= 0:
            continue
        best, best_name = n, None
        for name, pct in pcts.items():
            tgt = closes[i] * (1.0 + pct)
            j = next((k for k in range(i + 1, min(i + h, n - 1) + 1)
                      if (highs[k] >= tgt if pct > 0 else lows[k] <= tgt)), None)
            if j is not None and (j < best or (j == best and pct < pcts[best_name])):
                best, best_name = j, name
        total += 1
        if best_name is None:
            none += 1
        else:
            first[best_name] += 1
    if total < MIN_N:
        return {"n": total}
    return {"n": total,
            "first": {k: round(100 * v / total, 1) for k, v in first.items()},
            "none_pct": round(100 * none / total, 1)}


def historical_vol(closes: List[float], window: int) -> Optional[float]:
    """Annualized HV (stdev of daily log returns * sqrt(252) * 100)."""
    lr = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - window), len(closes))
          if closes[i - 1] > 0 and closes[i] > 0]
    return round(statistics.stdev(lr) * math.sqrt(252) * 100, 1) if len(lr) >= 10 else None


def max_drawdown(closes: List[float], window: int = 252) -> Optional[float]:
    seg = closes[-window:] if len(closes) >= window else closes
    if len(seg) < MIN_N:
        return None
    peak, worst = seg[0], 0.0
    for c in seg:
        peak = max(peak, c)
        if peak > 0:
            worst = min(worst, c / peak - 1.0)
    return round(worst * 100, 1)


def beta_corr(tkr_closes: List[float], spy_closes: List[float], window: int = 252) -> Tuple[Optional[float], Optional[float]]:
    """Beta and Pearson correlation of daily returns over the trailing window (aligned lengths)."""
    m = min(len(tkr_closes), len(spy_closes))
    t, s = tkr_closes[-m:], spy_closes[-m:]
    rt = [t[i] / t[i - 1] - 1 for i in range(m - 1) if t[i - 1] > 0][-window:]
    rs = [s[i] / s[i - 1] - 1 for i in range(m - 1) if s[i - 1] > 0][-window:]
    n = min(len(rt), len(rs))
    if n < MIN_N:
        return None, None
    rt, rs = rt[-n:], rs[-n:]
    mt, ms = statistics.mean(rt), statistics.mean(rs)
    var_s = sum((x - ms) ** 2 for x in rs)
    var_t = sum((x - mt) ** 2 for x in rt)
    cov = sum((rt[i] - mt) * (rs[i] - ms) for i in range(n))
    beta = cov / var_s if var_s > 0 else None
    corr = cov / math.sqrt(var_t * var_s) if var_t > 0 and var_s > 0 else None
    return (round(beta, 2) if beta is not None else None,
            round(corr, 2) if corr is not None else None)


def relative_strength(tkr_closes: List[float], ref_closes: List[float], bars: int) -> Optional[float]:
    """Excess return of ticker over ref in percentage points across *bars* bars."""
    m = min(len(tkr_closes), len(ref_closes))
    if m <= bars:
        return None
    t, s = tkr_closes[-m:], ref_closes[-m:]
    if t[-1 - bars] <= 0 or s[-1 - bars] <= 0:
        return None
    return round((t[-1] / t[-1 - bars] - s[-1] / s[-1 - bars]) * 100, 1)


def avg_dollar_volume(closes: List[float], volumes: List[float], window: int = 20) -> Optional[float]:
    m = min(len(closes), len(volumes))
    seg = [closes[i] * volumes[i] for i in range(m - window, m) if i >= 0]
    return round(sum(seg) / len(seg), 0) if len(seg) >= max(5, window // 2) else None


def _atr14(bars: List[Dict[str, Any]]) -> Optional[float]:
    from src.logic.deep_research.deterministic import atr_from_bars

    return atr_from_bars(bars)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _sector_etf(ticker: str, cache_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """(sector name, sector ETF) via yfinance info + the SECTOR_ETF map."""
    try:
        meta_path = Path(cache_dir) / f"{ticker.replace(':', '_')}_sector.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("date") == date.today().isoformat():
                    return meta.get("sector"), meta.get("etf")
            except Exception:
                pass
        from src.logic.prefetch_facts import SECTOR_ETF

        import yfinance as yf
        sector = (yf.Ticker(ticker).info or {}).get("sector")
        etf = SECTOR_ETF.get(sector or "")
        try:
            meta_path.write_text(json.dumps({"date": date.today().isoformat(), "sector": sector, "etf": etf}),
                                 encoding="utf-8")
        except Exception:
            pass
        return sector, etf
    except Exception as e:
        logger.debug(f"[{ticker}] sector lookup failed: {e}")
        return None, None


def _median_earnings_move(dw: Dict[str, Any]) -> Optional[float]:
    evs = [abs(float(ev["day_ret_pct"])) for ev in (dw.get("_earnings_reaction_events") or [])
           if isinstance(ev, dict) and ev.get("day_ret_pct") is not None]
    if evs:
        return round(statistics.median(evs), 2)
    try:
        v = float(dw.get("_historical_median_catalyst_move_pct") or 0)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def build_quant_facts(ticker: str, dw: Dict[str, Any], bars: List[Dict[str, Any]], *,
                      raw_dir: Optional[Path] = None, reg: Optional[Dict[str, Dict[str, Any]]] = None,
                      atr: Optional[float] = None, date_str: Optional[str] = None) -> Dict[str, Any]:
    """All code-computed stats in one dict. `bars` = DW CSV fallback rows; yfinance 10y preferred."""
    cache = _cache_dir(raw_dir)
    src = "datawindow"
    tk_bars, tk_src = daily_bars(ticker, cache)
    if len(tk_bars) >= len(bars or []):
        use = tk_bars
        src = f"{tk_src} ({len(tk_bars)} bars)"
    else:
        use = []
        for b in bars or []:  # datawindow DictReader rows are strings — coerce
            try:
                use.append({"time": str(b.get("time") or "")[:10], "open": float(b["open"]),
                            "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"]),
                            "volume": float(b.get("volume") or 0)})
            except (KeyError, TypeError, ValueError):
                continue
        src = f"data window csv ({len(use)} bars)"
    closes = [b["close"] for b in use]
    highs = [b["high"] for b in use]
    lows = [b["low"] for b in use]
    vols = [b.get("volume") or 0 for b in use]

    as_of = date_str or (str(use[-1]["time"]) if use else None)
    out: Dict[str, Any] = {"ticker": ticker, "as_of": as_of, "source": src, "n_bars": len(closes)}

    # Forward-return base rates
    out["horizon_base"] = {str(h): forward_returns(closes, h) for h in HORIZONS}

    # Empirical touch odds to every registry level + the plan-level race
    reg = reg or {}
    lv_prices = {k: float(v["price"]) for k, v in reg.items()
                 if isinstance(v, dict) and isinstance(v.get("price"), (int, float))}
    out["touch"] = {}
    for h in HORIZONS:
        ext = (_future_extreme(highs, h, "max"), _future_extreme(lows, h, "min"))
        for name, lv in lv_prices.items():
            r = touch_prob(highs, lows, closes, lv, h, ext)
            if r.get("pct") is not None:
                out["touch"].setdefault(name, {})[str(h)] = r
    plan_lv = {k: v for k, v in (("PINE_TARGET", (reg.get("PINE_TARGET") or {}).get("price")),
                                 ("PINE_STOP", (reg.get("PINE_STOP") or {}).get("price")),
                                 ("PINE_ZONE_HIGH", (reg.get("PINE_ZONE_HIGH") or {}).get("price"))) if v}
    out["touch_race"] = {str(h): first_touch_race(highs, lows, closes, plan_lv, h) for h in HORIZONS}

    # Volatility / drawdown
    out["hv20"], out["hv60"] = historical_vol(closes, 20), historical_vol(closes, 60)
    atr_v = atr or _atr14(use)
    out["atr14"] = round(atr_v, 2) if atr_v else None
    out["atr_pct"] = round(100 * atr_v / closes[-1], 2) if atr_v and closes else None
    out["max_dd_1y"] = max_drawdown(closes, 252)
    hi52 = max(highs[-252:]) if highs else None
    out["dd_from_52w"] = round(100 * (closes[-1] / hi52 - 1), 1) if hi52 and closes else None

    # SPY / sector
    spy_bars, spy_src = daily_bars("SPY", cache)
    spy_cl = [b["close"] for b in spy_bars]
    out["beta"], out["corr_spy"] = beta_corr(closes, spy_cl)
    out["rs_spy"] = {k: relative_strength(closes, spy_cl, b) for k, b in
                     (("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252))}
    # SPY regime + VIX (same bars the cache already holds — the regime_strip inputs)
    if len(spy_cl) >= 200:
        sma200 = sum(spy_cl[-200:]) / 200
        out["spy_regime"] = {"spy": round(spy_cl[-1], 2), "sma200": round(sma200, 2),
                             "above_200sma": bool(spy_cl[-1] > sma200),
                             "r5": round(100 * (spy_cl[-1] / spy_cl[-6] - 1), 1) if len(spy_cl) > 5 else None,
                             "r21": round(100 * (spy_cl[-1] / spy_cl[-22] - 1), 1) if len(spy_cl) > 21 else None}
    else:
        out["spy_regime"] = {}
    try:
        vix_bars, _ = daily_bars("^VIX", cache)
        vix_cl = [b["close"] for b in vix_bars]
        if vix_cl:
            out["vix"] = round(vix_cl[-1], 1)
            if len(vix_cl) >= 21:
                out["vix_21d_avg"] = round(sum(vix_cl[-21:]) / 21, 1)
    except Exception as e:
        logger.debug(f"[{ticker}] VIX fetch skipped: {e}")
    sector, etf = _sector_etf(ticker, cache)
    out["sector"], out["sector_etf"] = sector, etf
    out["rs_sector"] = {}
    if etf:
        sec_bars, _ = daily_bars(etf, cache)
        sec_cl = [b["close"] for b in sec_bars]
        out["rs_sector"] = {k: relative_strength(closes, sec_cl, b) for k, b in
                            (("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252))}
    out["dollar_vol_20d"] = avg_dollar_volume(closes, vols)
    out["earnings_move_median"] = _median_earnings_move(dw)
    out["earnings_moves"] = [{"date": str(ev.get("date") or "")[:10],
                              "pct": round(abs(float(ev["day_ret_pct"])), 2)}
                             for ev in (dw.get("_earnings_reaction_events") or [])
                             if isinstance(ev, dict) and ev.get("day_ret_pct") is not None][-16:]

    # Model-estimate touch odds for the plan levels (sigma model — never an empirical rate)
    out["model_touch"] = {}
    try:
        from src.logic.scenario_plans import daily_sigma, touch_odds

        spot = closes[-1] if closes else 0
        em = float(dw.get("Exp Move Pct 21b") or 0)
        sig = daily_sigma(spot, em, atr_v or 0)
        for name, lv in plan_lv.items():
            p, med = touch_odds(spot, lv, sig, 21)
            if p is not None:
                out["model_touch"][name] = {"p_touch_21b": p, "median_sessions": med,
                                            "kind": "model_estimate"}
    except Exception as e:
        logger.debug(f"[{ticker}] sigma touch estimate skipped: {e}")

    # Macro calendar (deterministic Fed/BLS schedule + Finnhub ~2 weeks out)
    try:
        from src.clients.macro_client import get_fomc_alert, get_upcoming_macro_events

        out["macro_timeline"] = get_upcoming_macro_events(date_str)
        alert = get_fomc_alert()
        if alert:
            out["macro_alert"] = alert
    except Exception as e:
        logger.debug(f"[{ticker}] macro dates skipped: {e}")
    return out


def touch_odds_for(qf: Dict[str, Any], level_name: str, horizon_bars: int) -> Optional[Dict[str, Any]]:
    """Empirical base-rate odds for *level_name* at the horizon nearest *horizon_bars*."""
    table = (qf or {}).get("touch") or {}
    per_level = table.get(str(level_name).upper()) or {}
    if not per_level:
        return None
    h = min(HORIZONS, key=lambda x: abs(x - horizon_bars))
    return per_level.get(str(h))


def _rate(d: Optional[Dict[str, Any]]) -> str:
    if not d:
        return "unavailable"
    if d.get("p50") is None:
        return f"insufficient history (n={d.get('n', 0)})"
    return f"p10 {d['p10']:+.1f}% · p50 {d['p50']:+.1f}% · p90 {d['p90']:+.1f}% (n={d['n']})"


def format_quant_facts(qf: Dict[str, Any]) -> str:
    """The citable 'Quant facts (code)' SOURCES section."""
    if not qf or not qf.get("n_bars"):
        return ""
    lines = [f"QUANT FACTS (code) — {qf['ticker']}, {qf['n_bars']} daily bars, {qf['source']}, as of {qf.get('as_of')}",
             "Forward returns (all historical starts):"]
    for h in HORIZONS:
        lines.append(f"- {h} bars: {_rate(qf['horizon_base'].get(str(h)))}")
    tk = qf.get("touch") or {}
    if tk:
        lines.append("Empirical touch odds (share of starts where price touched the level within h bars; n = starts):")
        for name in sorted(tk):
            per_h = tk[name]
            cells = [f"{h}b {per_h[str(h)]['pct']}% (n={per_h[str(h)]['n']})" for h in HORIZONS if str(h) in per_h]
            if cells:
                lines.append(f"- {name}: " + " · ".join(cells))
    for h in HORIZONS:
        race = (qf.get("touch_race") or {}).get(str(h)) or {}
        if race.get("first"):
            first = ", ".join(f"{k} {v}%" for k, v in race["first"].items())
            lines.append(f"First touched within {h} bars: {first}; none {race.get('none_pct')}% (n={race['n']})")
    stats = [
        f"HV20 {qf['hv20']}%" + (f" · HV60 {qf['hv60']}%" if qf.get("hv60") else "") if qf.get("hv20") else None,
        f"ATR14 {qf['atr14']}" + (f" ({qf['atr_pct']}% of price)" if qf.get("atr_pct") else "") if qf.get("atr14") else None,
        f"max drawdown 1y {qf['max_dd_1y']}%" if qf.get("max_dd_1y") is not None else None,
        f"from 52-week high {qf['dd_from_52w']}%" if qf.get("dd_from_52w") is not None else None,
        f"beta vs SPY {qf['beta']} · correlation {qf['corr_spy']} (1y daily)" if qf.get("beta") is not None else None,
    ]
    lines += [s for s in stats if s]
    rs_spy = {k: v for k, v in (qf.get("rs_spy") or {}).items() if v is not None}
    if rs_spy:
        lines.append("Relative strength vs SPY: " + " · ".join(f"{k} {v:+.1f}pp" for k, v in rs_spy.items()))
    rs_sec = {k: v for k, v in (qf.get("rs_sector") or {}).items() if v is not None}
    if rs_sec:
        lines.append(f"Relative strength vs sector {qf.get('sector_etf')} ({qf.get('sector')}): "
                     + " · ".join(f"{k} {v:+.1f}pp" for k, v in rs_sec.items()))
    reg = qf.get("spy_regime") or {}
    if reg.get("spy") is not None:
        lines.append(f"SPY regime: {reg['spy']} {'above' if reg.get('above_200sma') else 'below'} 200SMA {reg.get('sma200')}"
                     + (f" · 5d {reg['r5']:+.1f}% · 21d {reg['r21']:+.1f}%" if reg.get("r5") is not None else ""))
    if qf.get("vix") is not None:
        lines.append(f"VIX {qf['vix']}" + (f" (21d avg {qf['vix_21d_avg']})" if qf.get("vix_21d_avg") else ""))
    if qf.get("dollar_vol_20d"):
        lines.append(f"Average dollar volume 20d: ${qf['dollar_vol_20d']:,.0f}")
    if qf.get("earnings_move_median") is not None:
        lines.append(f"Earnings 1-day reaction, median |move|: {qf['earnings_move_median']}%")
    mt = qf.get("model_touch") or {}
    if mt:
        lines.append("Sigma-model touch estimates (model estimate, not an empirical rate): "
                     + ", ".join(f"{k} {v['p_touch_21b']}% within 21b" for k, v in mt.items()))
    if qf.get("macro_timeline"):
        lines.append(str(qf["macro_timeline"]))
    if qf.get("macro_alert"):
        lines.append(str(qf["macro_alert"]))
    return "\n".join(lines) + "\n"
