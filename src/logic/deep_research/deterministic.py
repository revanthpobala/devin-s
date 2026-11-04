"""Research contract: the writer names levels and structure; code owns every price.

Every failure in the Nov 2 2026 report audit was an LLM-authored number that a join or an assert would have produced
correctly (21 of 22 reports named a stop that disagreed with the stored stop). So:
- the side is chosen by code before any writing, one side per document;
- every level field is a registry name with a source column; a numeric level is banned. The registry spans the whole
  research packet (Pine, options/GEX, conformal bands, HTF, key S/R, gaps, volume profile, daily OHLC), not Pine alone;
- a number in prose must carry a citation ([S<n>] source section, [DW:<column>], [L:<LEVEL>]) and must appear in what it
  cites; Model A/B research is not citable, since its numbers are LLM-authored;
- the emitted object is asserted as-is; nothing is repaired, widened or clamped;
- the adversarial pass answers PASS or FAIL; a FAIL reason is displayed, never parsed into the plan;
- any failure closes the document: CASH_SKIP, no ticket, no armed scenario.
"""
from __future__ import annotations

import copy
import html
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SIDE_LONG = "LONG"
VERDICTS = ("ENTER", "ARMED", "WATCH", "WATCH (EVENT RISK)", "CASH_SKIP")
MANAGE_VERDICTS = ("HOLD", "TRIM", "EXIT")
TRIM_AMOUNTS = ("NONE", "THIRD", "HALF", "ALL")
VETOES = ("NONE", "EVENT_RISK", "HARD_FUNDAMENTAL")
STANCES = ("NONE", "EXIT_BEFORE", "HOLD_THROUGH", "EARNINGS_PLAY")
ENTRY_KINDS = ("ZONE", "LIMIT", "BREAKOUT", "NEXT_OPEN", "NONE")
SCENARIO_KINDS = ("PULLBACK", "BREAKOUT")
STANCE_WINDOW_DAYS = 21
RSI2_HOLD_DAYS = 10
PRICE_TOL = 0.005

# v2 writer fields
BBB_CASES = ("BULL", "BASE", "BEAR")
HORIZON_KEYS = ("1W", "1M", "3M", "6M", "1Y", "MULTI_YEAR")
HORIZON_BARS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}
HORIZON_STANCES = ("FAVORABLE", "NEUTRAL", "UNFAVORABLE", "NOT_ASSESSED")
STYLE_FITS = ("SWING", "POSITION", "LONG_TERM", "AVOID")

DW_LEVELS = (
    ("PINE_ZONE_LOW", "Long Entry Zone Bot"), ("PINE_ZONE_HIGH", "Long Entry Zone Top"),
    ("PINE_STOP", "Long Stop Loss"), ("PINE_TARGET", "Long Target"), ("PINE_T1_WAYPOINT", "Long Target T1 Waypoint"),
    ("MA20", "MA 20 Fast"), ("MA50", "MA 50 Mid"), ("MA150", "Weinstein MA 150"), ("MA200", "MA 200 Slow"),
    ("AVWAP_SUPPORT", "AVWAP Support"), ("AVWAP_RESISTANCE", "AVWAP Resistance"),
    ("VP_POC", "VP POC"), ("VP_VAL", "VP VAL"), ("VP_VAH", "VP VAH"),
    ("VP_HVN_ABOVE", "VP HVN Above"), ("VP_HVN_BELOW", "VP HVN Below"),
    ("DARVAS_BOX_TOP", "Darvas Box Top"), ("HIGH_52W", "high_52w"), ("LOW_52W", "low_52w"), ("EMA21", "ema21"),
    ("RSI2_CEILING", "RSI2 Entry Or Opening Ceiling"), ("RSI2_STOP", "RSI2 Fixed Stop"), ("RSI2_TARGET", "RSI2 Fixed Target"),
    ("OPT_CALL_WALL", "options_call_wall"), ("OPT_PUT_WALL", "options_put_wall"), ("OPT_GAMMA_FLIP", "options_zero_gamma_flip"),
    ("CONF_FLOOR_14D", "conformal_90_floor_14d"), ("CONF_FLOOR_21D", "conformal_90_floor_21d"),
    ("CONF_CEILING_21D", "conformal_90_ceiling_21d"), ("CONF_STOP_14D", "conformal_certified_stop_14d"),
)
DW_LIST_LEVELS = (("KEY_SUPPORT", "_underlying_key_supports"), ("KEY_RESISTANCE", "_overhead_key_resistances"))

_FILING = r"(?:8-K|10-K|10-Q|13F|13D|13G|S-1|S-3|20-F|6-K)"
_MASK = re.compile(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b{_FILING}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUM = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?|\.\d+)")
_CITE = re.compile(r"\[(S\d+|DW:[^\]]+|L:[A-Za-z0-9_]+)\]")
_GAP = re.compile(r"Gap (Up|Down) \[\$?([\d.]+)\s*\S\s*\$?([\d.]+)\]")
_POS_NONE = re.compile(r"POSITION:\s*(?:NONE|UNKNOWN)\b|no current holdings|no active", re.I)


def _has_position(block: Any) -> bool:
    """A broker-position block that actually holds shares — NONE/UNKNOWN/'no current holdings' do not."""
    return bool(block) and not _POS_NONE.search(str(block))


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _fnum(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def numbers(text: Any) -> List[str]:
    """Numeric tokens in prose, outside ISO dates, filings (8-K) and identifiers (MA200, Q3)."""
    masked = _MASK.sub(lambda m: " " * len(m.group(0)), str(text or ""))
    return [m.group(1).replace(",", "") for m in _NUM.finditer(masked)]


def _matches(tok: str, values: List[float]) -> bool:
    d = len(tok.split(".")[1]) if "." in tok else 0
    v = float(tok)
    return any(abs(round(e, d) - v) < 1e-9 or abs(round(abs(e), d) - v) < 1e-9 for e in values)


def _scalars(v: Any) -> List[float]:
    if isinstance(v, dict):
        return [x for e in v.values() for x in _scalars(e)]
    if isinstance(v, (list, tuple)):
        return [x for e in v for x in _scalars(e)]
    if isinstance(v, str):
        return [float(t) for t in numbers(v)]
    try:
        return [float(v)]
    except (TypeError, ValueError):
        return []


def _cite_values(cite: str, sections: List[Tuple[str, str]], dw: Dict[str, Any],
                 reg: Dict[str, Dict[str, Any]]) -> Optional[List[float]]:
    if cite.startswith("S"):
        i = int(cite[1:])
        return [float(t) for t in re.findall(r"\d+(?:\.\d+)?|\.\d+", sections[i][1].replace(",", ""))] if i < len(sections) else None
    if cite.startswith("DW:"):
        col = cite[3:].strip()
        return _scalars(dw[col]) if col in dw else None
    lv = reg.get(cite[2:].upper())
    return [lv["price"]] if lv else None


def _find(t: str, is_date: bool, sections: List[Tuple[str, str]], dw: Dict[str, Any],
          reg: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Name of the first source that contains token t, else None."""
    for i, (title, body) in enumerate(sections):
        if (t in str(body)) if is_date else _matches(t, _cite_values(f"S{i}", sections, dw, reg) or []):
            return f"S{i}"
    if is_date:
        return next((f"DW:{k}" for k, v in dw.items() if t in str(v)), None)
    hit = next((k for k, v in dw.items() if (not isinstance(v, str) or len(v) < 400) and _matches(t, _scalars(v))), None)
    if hit:
        return f"DW:{hit}"
    return next((f"L:{k}" for k, v in reg.items() if _matches(t, [v["price"]])), None)


def trace(field: str, text: str, sections: List[Tuple[str, str]], dw: Dict[str, Any],
          reg: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every number and ISO date must appear in a source. The writer's citation is checked first; if the number is not
    there, every other source is searched and the real one is recorded. A number in no source fails."""
    rows: List[Dict[str, Any]] = []
    fails: List[str] = []
    pos = 0
    for m in [*_CITE.finditer(text), None]:
        seg = text[pos:m.start() if m else len(text)]
        dates = _DATE.findall(seg)
        toks = numbers(seg) + dates
        pos = m.end() if m else len(text)
        cited = m.group(1) if m else None
        vals = _cite_values(cited, sections, dw, reg) if cited else None
        raw = (sections[int(cited[1:])][1] if cited.startswith("S") and int(cited[1:]) < len(sections) else
               str(dw.get(cited[3:].strip())) if cited.startswith("DW:") else "") if cited else ""
        for t in toks:
            is_date = t in dates
            ok = vals is not None and ((t in raw) if is_date else _matches(t, vals))
            src = cited if ok else _find(t, is_date, sections, dw, reg)
            if src is None:
                fails.append(f"{field}: {t} appears in no source")
                continue
            rows.append({"field": field, "number": t, "cite": src,
                         "source": sections[int(src[1:])][0] if src.startswith("S") else src})
    return rows, fails


def _bar_levels(bars: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    hl = [(_f(b.get("high")), _f(b.get("low"))) for b in bars or []]
    hl = [x for x in hl if x[0] and x[1]]
    if len(hl) >= 2:
        out["PREV_HIGH"] = {"price": round(hl[-2][0], 2), "source": "csv: prior bar high"}
        out["PREV_LOW"] = {"price": round(hl[-2][1], 2), "source": "csv: prior bar low"}
    for n in (20, 60):
        if len(hl) >= n:
            out[f"SWING_HIGH_{n}"] = {"price": round(max(h for h, _ in hl[-n:]), 2), "source": f"csv: highest high of last {n} bars"}
            out[f"SWING_LOW_{n}"] = {"price": round(min(lo for _, lo in hl[-n:]), 2), "source": f"csv: lowest low of last {n} bars"}
    return out


def atr_from_bars(bars: Optional[List[Dict[str, Any]]], n: int = 14) -> Optional[float]:
    rows = [(_f(b.get("high")), _f(b.get("low")), _f(b.get("close"))) for b in bars or []]
    rows = [r for r in rows if all(r)]
    if len(rows) <= n:
        return None
    tr = [max(h - lo, abs(h - rows[i - 1][2]), abs(lo - rows[i - 1][2])) for i, (h, lo, _) in enumerate(rows) if i]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def registry(dw: Dict[str, Any], atr: Optional[float],
             bars: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    from src.logic.pine_levels import pine_long_plan, rsi2_order_live

    pine = pine_long_plan(dw) or {}
    live = {"PINE_ZONE_LOW": pine.get("zone_low"), "PINE_ZONE_HIGH": pine.get("zone_high"), "PINE_STOP": pine.get("stop"),
            "PINE_TARGET": pine.get("target"), "PINE_T1_WAYPOINT": pine and _f(dw.get("Long Target T1 Waypoint"))}
    rsi2 = rsi2_order_live(dw)
    reg: Dict[str, Dict[str, Any]] = {}
    for name, col in DW_LEVELS:
        if name.startswith("RSI2_") and not rsi2:
            continue
        p = live[name] if name in live else _f(dw.get(col))
        if p:
            reg[name] = {"price": round(p, 2), "source": col}
    for prefix, col in DW_LIST_LEVELS:
        vals = dw.get(col)
        for i, v in enumerate(vals if isinstance(vals, list) else []):
            if _f(v):
                reg[f"{prefix}_{i + 1}"] = {"price": round(_f(v), 2), "source": f"{col}[{i}]"}
    htf = dw.get("_htf_levels")
    for k, v in (htf.items() if isinstance(htf, dict) else []):
        if _f(v):
            reg["HTF_" + re.sub(r"\W+", "_", str(k)).strip("_").upper()] = {"price": round(_f(v), 2), "source": f"_htf_levels[{k}]"}
    gaps = dw.get("unfilled_gaps")
    for i, g in enumerate(gaps if isinstance(gaps, list) else []):
        m = _GAP.search(str(g))
        if m:
            lo, hi = sorted((float(m.group(2)), float(m.group(3))))
            tag = f"GAP_{m.group(1).upper()}_{i + 1}"
            reg[f"{tag}_LOW"] = {"price": round(lo, 2), "source": f"unfilled_gaps[{i}]"}
            reg[f"{tag}_HIGH"] = {"price": round(hi, 2), "source": f"unfilled_gaps[{i}]"}
    reg.update(_bar_levels(bars))
    close = _f(dw.get("close") or dw.get("Close"))
    if close:
        reg["CLOSE"] = {"price": round(close, 2), "source": "close"}
    base_name = "PINE_ZONE_LOW" if "PINE_ZONE_LOW" in reg else "CLOSE"
    pine_st = reg.get("PINE_STOP", {}).get("price")
    if base_name in reg and atr:
        from src.tracking.trade_rules import STOP_ATR_MIN

        floor_st = round(reg[base_name]["price"] - STOP_ATR_MIN * atr, 2)
        reg["ATR_FLOOR_STOP"] = ({"price": pine_st, "source": f"PINE_STOP (already {STOP_ATR_MIN:g} ATR below {base_name})"}
                                 if pine_st and pine_st <= floor_st else
                                 {"price": floor_st, "source": f"computed: {base_name} - {STOP_ATR_MIN:g} x ATR14"})
    st = reg.get("ATR_FLOOR_STOP", {}).get("price") or pine_st
    tg = reg.get("PINE_TARGET", {}).get("price")
    if st and tg and tg > st:
        from src.logic.scenario_plans import _rr_floor

        r = _rr_floor()
        reg["RR_FLOOR_LIMIT"] = {"price": round((tg + r * st) / (1 + r), 2),
                                 "source": f"computed: (PINE_TARGET + {r:g} x ATR_FLOOR_STOP) / {1 + r:g}"}
    return reg


def choose_side(dw: Dict[str, Any], setup_lane: Optional[str], held: bool = False) -> Optional[str]:
    """Document side, set before writing. Shorts carry no measured edge, so the only side is LONG, and only with a plan.
    A held name always gets the LONG side: without an entry plan the contract runs in manage mode."""
    from src.logic.pine_levels import pine_long_plan, rsi2_order_live

    if pine_long_plan(dw) or held:
        return SIDE_LONG
    if setup_lane == "RSI2" and rsi2_order_live(dw):
        return SIDE_LONG
    return None


def bar_is_forming(bar_date: Any, captured_at: datetime, close_hm: Tuple[int, int]) -> bool:
    return str(bar_date or "")[:10] == captured_at.date().isoformat() and (captured_at.hour, captured_at.minute) < close_hm


def ladder(reg: Dict[str, Dict[str, Any]], atr: Optional[float]) -> str:
    close = reg.get("CLOSE", {}).get("price")
    rows = []
    for name, v in sorted(reg.items(), key=lambda kv: -kv[1]["price"]):
        rel = (f"{(v['price'] - close) / atr:+.2f} ATR, {100 * (v['price'] - close) / close:+.2f}% vs close"
               if close and atr and name != "CLOSE" else "close")
        rows.append(f"{name} {v['price']:.2f} ({rel}; {v['source']})")
    return "\n".join(rows)


def _allowed_numbers(reg: Dict[str, Dict[str, Any]], dw: Dict[str, Any], sections: List[Tuple[str, str]]) -> List[float]:
    vals = [v["price"] for v in reg.values()]
    vals += [x for v in dw.values() if not isinstance(v, str) or len(v) < 400 for x in _scalars(v)]
    for _, body in sections:
        vals += [float(t) for t in re.findall(r"\d+(?:\.\d+)?|\.\d+", str(body).replace(",", ""))]
    return vals


_DOLLAR = re.compile(r"(?<![\w.])\$(\d[\d,]*(?:\.\d+)?)(\s?(?:[BMKT]\b|billion|million|thousand|trillion))?", re.I)


def annotate(text: str, allowed: List[float]) -> Tuple[str, int]:
    """Mark per-share dollar figures that match no source, data column or level. Nothing is removed or rewritten."""
    count = 0

    def mark(m: "re.Match[str]") -> str:
        nonlocal count
        tok = m.group(1).replace(",", "")
        if m.group(2) or _matches(tok, allowed):
            return m.group(0)
        count += 1
        return m.group(0) + " ⚠️"

    return _DOLLAR.sub(mark, text), count


def code_facts(dw: Dict[str, Any], reg: Dict[str, Dict[str, Any]], atr: Optional[float], forming: bool,
               e_date: Optional[str], e_days: Optional[int], ts: Dict[str, Any],
               bars: Optional[List[Dict[str, Any]]] = None) -> str:
    """Section S0: numbers code computed or read from the export. Citable like any source."""
    keys = ("Exp Move Pct 21b", "Energy IV Rank Pct", "Ext Z Self Relative", "Ext Pct vs MA200", "Long RR At Market",
            "Action Long Code", "Stage")
    lines = [f"Bar {dw.get('bar_date') or dw.get('time')} ({'forming' if forming else 'closed'})",
             f"ATR14 {atr:.2f}" if atr else "ATR14 unavailable", f"Pine state {ts.get('label')}"]
    lines += [f"{k} {_fmt(dw.get(k))}" for k in keys if dw.get(k) is not None]
    if not forming and dw.get("RVOL Vs Avg") is not None:
        lines.append(f"RVOL Vs Avg {_fmt(dw.get('RVOL Vs Avg'))}")
    if e_date:
        lines.append(f"Next earnings {e_date}" + (f", {e_days} trading days" if e_days is not None else ""))
    rx = [ev for ev in dw.get("_earnings_reaction_events") or [] if isinstance(ev, dict)]
    lines += [f"Earnings {ev.get('date')}: EPS {ev.get('eps_reported')} vs est {ev.get('eps_estimate')}, surprise "
              f"{ev.get('surprise_pct')}%, gap {ev.get('gap_pct')}%, day {ev.get('day_ret_pct')}%" for ev in rx[-4:]]
    lines += chart_truth(dw, reg, atr, bars or [], forming)
    return "\n".join(lines) + "\n\nLEVEL LADDER (high to low)\n" + ladder(reg, atr)


def chart_truth(dw: Dict[str, Any], reg: Dict[str, Dict[str, Any]], atr: Optional[float],
                bars: List[Dict[str, Any]], forming: bool) -> List[str]:
    """What the bar did against the levels, and what the export's names actually mean."""
    col = lambda b, k: _f(b.get(k))
    closes = [x for x in (col(b, "close") for b in bars) if x]
    out = ["MA 20 Fast / MA 50 Mid / MA 200 Slow are EMAs; sma20 / sma50 / sma200 are simple averages",
           "Weinstein MA 150 is a 150-bar Hull MA (fast), not Weinstein's 30-week SMA"]
    if not bars:
        return out
    b, lo, c = bars[-1], col(bars[-1], "low"), col(bars[-1], "close")
    stop = (reg.get("PINE_STOP") or {}).get("price")
    if lo and stop and atr:
        out.append(f"Bar low {lo:.2f} is {abs(lo - stop) / atr:.2f} ATR {'above' if lo > stop else 'at or BELOW'} PINE_STOP {stop:.2f}"
                   + ("; this bar already traded through the stop" if lo <= stop else ""))
    if len(bars) >= 2 and lo and c:
        pb, pt = col(bars[-2], "Long Entry Zone Bot"), col(bars[-2], "Long Entry Zone Top")
        if pb and pt:
            st = "held" if lo >= pb else ("pierced, closed back inside or above" if c >= pb else "BROKEN, closed below it")
            out.append(f"Prior bar zone {pb:.2f}-{pt:.2f}: {st}")
    if len(closes) >= 161:
        sma = lambda i: sum(closes[len(closes) - 150 - i:len(closes) - i]) / 150
        s0, s10 = sma(0), sma(10)
        out.append(f"30-week SMA (150-day) {s0:.2f}, {'rising' if s0 > s10 else 'falling'} over 10 bars; "
                   f"close {'above' if c > s0 else 'below'} it")
    if len(closes) >= 30 and c:
        up = dn = 0.0
        prev = None
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            up, dn = up * 0.5 + max(d, 0) * 0.5, dn * 0.5 + max(-d, 0) * 0.5
            prev, cur = (None if i == 1 else cur), (100.0 if dn == 0 else 100 - 100 / (1 + up / dn))
        ema200 = _f(dw.get("MA 200 Slow"))
        if forming and prev is not None and cur < 10 <= prev and ema200 and c > ema200:
            out.append(f"RSI2 {cur:.2f} crossed below 10 above EMA200 on this forming bar; Pine arms the RSI2 order only "
                       "at the daily close")
    sups = [x for x in (dw.get("_underlying_key_supports") or []) if isinstance(x, (int, float))]
    hit = [f"{x:.2f}" for x in sups if lo and lo < x]
    if hit:
        out.append(f"Key supports traded through this bar: {', '.join(hit)}")
    return out


WRITER_SYS = """You write the research decision for {ticker}, side {side}. Code owns every price. You choose levels by NAME from
the LEVEL LADDER in [S0] (Pine, options walls, conformal bands, HTF averages, key support/resistance, gaps, volume profile,
daily highs/lows) and write the reasoning. Output one JSON object and nothing else:
{{"side": "{side}",
 "verdict": "ENTER|ARMED|WATCH|WATCH (EVENT RISK)|CASH_SKIP — or HOLD|TRIM|EXIT when POSITION MODE is MANAGE",
 "veto": "NONE|EVENT_RISK|HARD_FUNDAMENTAL",
 "veto_fact": "dated fact behind a veto, else empty",
 "entry": {{"kind": "ZONE|LIMIT|BREAKOUT|NEXT_OPEN|NONE", "low": "<LEVEL or null>", "high": "<LEVEL or null>", "trigger": "<LEVEL or null>"}},
 "stop": "<LEVEL>", "target_1": "<LEVEL>", "target_2": "<LEVEL or null>", "trim": "NONE|THIRD|HALF|ALL",
 "scenarios": [{{"kind": "PULLBACK|BREAKOUT", "trigger": "<LEVEL>", "zone_low": "<LEVEL or null>", "stop": "<LEVEL>",
                "target": "<LEVEL>", "window_end": "YYYY-MM-DD", "confirm": ["check"], "thesis": "one line"}}],
 "earnings_stance": "NONE|EXIT_BEFORE|HOLD_THROUGH|EARNINGS_PLAY",
 "earnings_rationale": "one line",
 "why_now": "why this setup matters now rather than later, 1-2 lines",
 "edge": "what the market may be overlooking — the variant perception, 1-2 lines",
 "technicals": ["the technical read from the Data Window and Quant facts: trend, key levels, volume, momentum, 3-5 lines"],
 "style": {{"best_fit": "SWING|POSITION|LONG_TERM|AVOID", "reason": "one line"}},
 "scenarios_bbb": [{{"case": "BULL|BASE|BEAR", "level": "<LEVEL>", "by": "YYYY-MM-DD",
                    "drivers": ["what gets it there"], "assumptions": ["what must hold"]}}],
 "catalysts": [{{"date": "YYYY-MM-DD", "event": "what happens, with a citation to the source that gives the date",
                 "if_good": "effect if it breaks well", "if_bad": "effect if it breaks badly"}}],
 "horizons": {{"1W": {{"stance": "FAVORABLE|NEUTRAL|UNFAVORABLE|NOT_ASSESSED", "why": "reason specific to this horizon"}},
              "1M": {{"stance": "...", "why": "..."}}, "3M": {{"stance": "...", "why": "..."}},
              "6M": {{"stance": "...", "why": "..."}}, "1Y": {{"stance": "...", "why": "..."}},
              "MULTI_YEAR": {{"stance": "...", "why": "..."}}}},
 "market_context": ["sector and macro context for this name, 2-4 lines; every line states the link to THIS ticker, at most one line may be generic macro"],
 "transmission": [{{"event": "dated event with [S<n>]", "status": "VERIFIED|SCENARIO",
                   "channel": "a CHANNEL name from the table below",
                   "chain": [{{"step": "one link of the causal path", "kind": "FACT|INFERENCE"}}],
                   "exposure": "how THIS ticker is exposed, cited", "direction": "HEADWIND|TAILWIND|MIXED",
                   "horizon": "1W|1M|3M|6M|1Y|MULTI_YEAR",
                   "confirm": [{{"symbol": "a proxy from the channel", "move": "UP|DOWN"}}],
                   "invalidate": "one line"}}],
 "risks": ["named risks with dates where known, 3-5 lines"],
 "wrong_if": "the single clearest falsifier of this thesis, 1-2 lines",
 "vs_alternatives": "versus doing nothing or holding SPY, weighing the quant base rates, 1-2 lines",
 "fundamentals": ["business and financial health from the Fundamentals, Earnings, Institutional and news sources, 5-8 lines: revenue and earnings growth, margins, valuation, balance sheet or liquidity, guidance, analyst targets and rating changes, insider and institutional flow"],
 "thesis": ["the case FOR the trade, 3-5 lines, weighing fundamentals and news together with the technicals"], "case_against": ["the case AGAINST, traps and risks, 3-5 lines, fundamental and news risks as well as technical"],
 "news_synthesis": ["dated company, sector and macro news that moves the stock, 4-8 lines, each with an ISO date"],
 "concurrence": ["what Report A and Report B agree on, 1-2 lines"],
 "conflicts": ["where Report A and Report B disagree and which side the sources support, 0-4 lines"]}}

TOOLS: get_candles gives today's live intraday candles. Call it with the ladder prices you are weighing (zone, stop,
targets, triggers) before you decide. When Report A/B or the SOURCES are thin, stale or contradictory on fundamentals,
news, filings or options, research it yourself with search_web, fetch_finnhub_news, fetch_sec_filings,
fetch_earnings_release, fetch_earnings_calendar, fetch_options_chain, fetch_gex or fetch_schwab_options_flow.
Every tool result becomes a SOURCES section; cite it like any source.

HARD RULES (code asserts every one; a violation in a level or plan field discards the plan; an untraceable narrative line is withheld):
1. Every level is a ladder NAME. Never write a price or any number in a level field.
2. Every number in a text field must be copied from a source and followed by its citation: [S<n>] for a SOURCES section,
   [DW:<exact column>] for a Data Window field, [L:<LEVEL>] for a ladder level. Example: "revenue grew 12% [S3]".
   Code checks each number against what it cites. RESEARCH INPUTS are not citable. Never compute a new number (no
   ratios, sums or distances you derived). Dates are written YYYY-MM-DD and cited the same way; names such as MA200,
   RSI2, Q3, 8-K need no citation.
3. LONG geometry: stop < entry low <= entry high < target_1 < target_2. Stop at or below ATR_FLOOR_STOP (NEXT_OPEN is
   exempt: its RSI2_STOP is the measured fixed stop). R:R at the entry
   high must reach the floor ({floor:g}); RR_FLOOR_LIMIT is the highest entry that does with ATR_FLOOR_STOP and PINE_TARGET,
   so when it sits within the Pine zone use it as the entry high. When it sits below PINE_ZONE_LOW no zone entry meets the
   floor: use entry kind NONE with a PULLBACK/BREAKOUT scenario, or CASH_SKIP. The stop you name is the only "stop":
   PINE_STOP and ATR_FLOOR_STOP are reference levels — never call them "the stop", and cite ATR_FLOOR_STOP touch
   odds only when the stop field is ATR_FLOOR_STOP.
4. ZONE/LIMIT entries rest at or below CLOSE; BREAKOUT triggers sit above CLOSE. NEXT_OPEN is only the RSI2 order with
   RSI2_CEILING / RSI2_STOP / RSI2_TARGET.
5. ENTER only when the STATE line says ACTIONABLE (or RSI2 order pending), the bar is CLOSED, earnings are outside the
   block window and veto is NONE. ARMED needs a resting entry below CLOSE, a BREAKOUT trigger, or a scenario.
6. Earnings within {block} trading days: veto EVENT_RISK and verdict WATCH (EVENT RISK) or CASH_SKIP.
   Earnings within {stance_days} trading days: earnings_stance must not be NONE.
   Narrative lists (fundamentals, thesis, case_against, news_synthesis, concurrence, conflicts) are always required in full, even for
   CASH_SKIP. Describe Report A/B disagreements in words or by [L:NAME] cite; do not copy numbers from the reports.
7. HARD_FUNDAMENTAL (guidance cut, fraud, accounting, pending deal, delisting) forces CASH_SKIP and no scenarios.
   CASH_SKIP carries entry kind NONE.
8. Scenarios: PULLBACK trigger below CLOSE with zone_low below the trigger and within one and a half ATR of it;
   BREAKOUT trigger above CLOSE. Stop at least the ATR floor below the fill, target within the expected-move cap,
   R:R at the trigger at least the floor. When unsure, CASH_SKIP with an empty plan is always valid.
9. scenarios_bbb: exactly one BULL, one BASE and one BEAR. `level` is a ladder NAME (code fills the price, the %
   move from the reference and the base-rate odds). `by` is a future ISO date. Bear below the reference (live or
   close) below bull, with base in between.
10. horizons: all six keys present; each `why` a different reason, specific to that horizon. The stance is for that
    horizon only. Day trading is never the style — this system trades daily/weekly setups and has no intraday edge.
11. catalysts: every `date` must appear in a cited source (the grounded catalyst calendar, the macro timeline or
    the earnings packet); cite the section in `event`.
12. transmission: 1-4 chains, each an event, then a channel, then the affected group, then this ticker. Every `event`
    carries a [S<n>] cite. VERIFIED needs a cited, dated source in `event`; without one the event is SCENARIO and
    its text must start with "If". FACT steps carry citations; INFERENCE steps contain no numbers. `channel` is a
    CHANNEL name from the table below and every `confirm` symbol is drawn from that channel's proxies. No
    probabilities. The chains are context only: they cannot change verdict, veto or levels.
13. POSITION MODE MANAGE (the user already holds this name): the verdict is HOLD, TRIM or EXIT.
    `stop` is a ladder NAME below the close — the protective exit for the shares, at least the ATR
    floor under the close. `target_1` and `target_2` are trim levels above the close. `trim` is
    NONE|THIRD|HALF|ALL: TRIM needs THIRD or HALF, EXIT needs ALL (or a named fraction), HOLD takes
    NONE. EXIT needs a cited reason in veto_fact or wrong_if. Entry geometry and the R:R floor do
    not apply — this is not an entry. When HAS_ENTRY_PLAN is yes you may instead write a normal
    ENTER/ARMED add plan under rules 3-8."""

ADVERSARY_SYS = (
    "You audit a trade plan for {ticker}. Read the SOURCES and the PLAN. Answer FAIL if any source fact contradicts the "
    "thesis, if a material dated event inside the hold (earnings, offering, guidance cut, investigation, ruling, deal) is "
    "ignored, or if the verdict is not supported by the sources. Otherwise answer PASS. Reply with the single word PASS, or with "
    "FAIL: followed by one plain sentence naming the source fact [S<n>] that breaks the plan."
)


def _llm(system: str, user: str, model_env: str, json_mode: bool, max_tokens: int) -> str:
    from src.clients.llm_client import query_local_llm

    model = os.getenv(model_env) or os.getenv("CHAT_MODEL") or None
    try:
        return query_local_llm(system_prompt=system, user_prompt=user, json_mode=json_mode, use_openrouter=True,
                               model=model, use_tools=False, max_tokens=max_tokens,
                               reasoning_effort=os.getenv("JUDGE_REASONING", "high")) or ""
    except Exception as e:
        logger.warning(f"{model_env} call failed: {e}")
        return ""


CANDLES_TOOL = {"type": "function", "function": {
    "name": "get_candles",
    "description": ("Live intraday candles for this ticker from the broker: day open/high/low/close, live quote, session "
                    "volume, and for every price you pass: crossed or not, first-touch time, held on close. Use it to see "
                    "what price is doing today at the ladder levels you are considering (zone, stop, targets, triggers). "
                    "Its output becomes a citable SOURCES section."),
    "parameters": {"type": "object", "properties": {
        "interval": {"type": "string", "enum": ["1m", "5m"]},
        "days": {"type": "integer", "description": "daily bars to include, default 5"},
        "levels": {"type": "array", "items": {"type": "number"}, "description": "ladder prices to evaluate"}}}}}


RESEARCH_TOOLS = ("search_web", "fetch_finnhub_news", "fetch_sec_filings", "fetch_earnings_release",
                  "fetch_earnings_calendar", "fetch_options_chain", "fetch_gex", "fetch_schwab_options_flow")
TOOL_OUTPUT_CAP = 12000


def _research_tool(name: str, a: Dict[str, Any], ticker: str) -> str:
    from types import SimpleNamespace
    from src.clients.llm_client import execute_tool_call

    if name != "search_web":
        a = {**a, "ticker": ticker}
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(a)))
    return str(execute_tool_call(call, chat_ticker=ticker))[:TOOL_OUTPUT_CAP]


def _writer(system: str, user: str, ticker: str, sections: List[Tuple[str, str]], max_rounds: int = 5) -> str:
    """Writer with get_candles plus research tools. Each tool result is appended to `sections` so its numbers are citable and traced."""
    from src.clients.llm_client import TOOLS, _build_client_and_model
    from src.tracking.candle_service import get_candles_tool

    tools = [CANDLES_TOOL] + [t for t in TOOLS if t.get("function", {}).get("name") in RESEARCH_TOOLS]

    try:
        client, model, provider = _build_client_and_model(True, model=os.getenv("WRITER_MODEL") or os.getenv("CHAT_MODEL"))
    except Exception as e:
        logger.warning(f"writer client unavailable: {e}")
        return ""
    extra = {"reasoning": {"effort": os.getenv("JUDGE_REASONING", "high")}} if provider == "openrouter" else {}
    msgs: List[Dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for rnd in range(max_rounds + 1):
        kw: Dict[str, Any] = {"model": model, "messages": msgs, "temperature": 0.0, "max_tokens": 24000, "seed": 42}
        if extra:
            kw["extra_body"] = extra
        if rnd < max_rounds:
            kw["tools"] = tools
        try:
            choice = client.chat.completions.create(**kw).choices[0]
            msg = choice.message
        except Exception as e:
            logger.warning(f"[{ticker}] writer call failed: {e}")
            return ""
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            if getattr(choice, "finish_reason", None) != "stop":
                logger.warning(f"[{ticker}] writer finish_reason={getattr(choice, 'finish_reason', None)} "
                               f"content={len(msg.content or '')} chars")
            return msg.content or ""
        msgs.append({"role": "assistant", "content": msg.content or "",
                     "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.function.name,
                                                                                 "arguments": c.function.arguments}} for c in calls]})
        for c in calls:
            try:
                a = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                a = {}
            if c.function.name in RESEARCH_TOOLS:
                try:
                    out = _research_tool(c.function.name, a, ticker)
                except Exception as e:
                    out = f"Error: {type(e).__name__}"
                sections.append((f"Judge research ({c.function.name} {json.dumps(a)[:120]})", out))
                logger.info(f"[{ticker}] writer tool {c.function.name} -> S{len(sections) - 1}")
                msgs.append({"role": "tool", "tool_call_id": c.id, "content": f"[S{len(sections) - 1}] cite this section as [S{len(sections) - 1}]\n{out}"})
                continue
            levels = [float(x) for x in a.get("levels") or [] if isinstance(x, (int, float, str)) and re.fullmatch(r"\s*[\d.]+\s*", str(x))]
            interval = a.get("interval") if a.get("interval") in ("1m", "5m") else "5m"
            try:
                out = get_candles_tool(ticker=ticker, interval=interval, days=max(1, min(int(a.get("days") or 5), 60)), levels=levels)
            except Exception as e:
                out = f"Error: {type(e).__name__}"
            sections.append((f"Intraday candles (get_candles {interval}, levels {levels})", out))
            logger.info(f"[{ticker}] writer tool get_candles({interval}, {levels}) -> S{len(sections) - 1}")
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": f"[S{len(sections) - 1}] cite this section as [S{len(sections) - 1}]\n{out}"})
    return ""


def parse_json(text: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def adversary_verdict(text: str) -> str:
    last = ([ln for ln in str(text or "").splitlines() if ln.strip()] or [""])[-1]
    return "PASS" if last.strip().strip(".*`\"'").upper() == "PASS" else "FAIL"


def adversary_has_verdict(text: str) -> bool:
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    return bool(lines) and (adversary_verdict(text) == "PASS" or bool(re.match(r"^\W*FAIL\b", lines[0], re.I)))


def adversary_reason(text: str) -> str:
    return " ".join(re.sub(r"^\W*FAIL\W*", "", str(text or "").strip(), flags=re.I).split())[:400]


def _level(reg: Dict[str, Dict[str, Any]], v: Any, field: str, fails: List[str], required: bool) -> Optional[Dict[str, Any]]:
    if v in (None, "", "null", "NONE"):
        if required:
            fails.append(f"{field}: missing level")
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\s*\$?[\d.,]+\s*", v)):
        fails.append(f"{field}: numeric level is banned ({v!r})")
        return None
    if not isinstance(v, str) or v.strip().upper() not in reg:
        fails.append(f"{field}: {v!r} is not a ladder level")
        return None
    name = v.strip().upper()
    return {"level": name, **reg[name]}


LIST_KEYS = ("fundamentals", "thesis", "case_against", "news_synthesis", "concurrence", "conflicts")
SOFT_SCALAR_KEYS = ("why_now", "edge", "wrong_if", "vs_alternatives")
SOFT_LIST_KEYS = ("technicals", "market_context", "risks")


def _prose_fields(obj: Dict[str, Any]) -> List[Tuple[str, str]]:
    out = [("veto_fact", obj.get("veto_fact")), ("earnings_rationale", obj.get("earnings_rationale"))]
    out += [(k, obj.get(k)) for k in SOFT_SCALAR_KEYS]
    for key in (*LIST_KEYS, *SOFT_LIST_KEYS):
        out += [(f"{key}[{i}]", t) for i, t in enumerate(obj.get(key) or [])]
    st = obj.get("style")
    if isinstance(st, dict):
        out.append(("style.reason", st.get("reason")))
    for i, s in enumerate(obj.get("scenarios_bbb") or []):
        if isinstance(s, dict):
            for field in ("drivers", "assumptions"):
                out += [(f"scenarios_bbb[{i}].{field}[{j}]", t) for j, t in enumerate(s.get(field) or [])]
    for i, c in enumerate(obj.get("catalysts") or []):
        if isinstance(c, dict):
            out += [(f"catalysts[{i}].{f}", c.get(f)) for f in ("event", "if_good", "if_bad")]
    for k, v in (obj.get("horizons") or {}).items():
        if isinstance(v, dict):
            out.append((f"horizons.{k}.why", v.get("why")))
    for i, s in enumerate(obj.get("scenarios") or []):
        if isinstance(s, dict):
            out.append((f"scenarios[{i}].thesis", s.get("thesis")))
            out += [(f"scenarios[{i}].confirm[{j}]", c) for j, c in enumerate(s.get("confirm") or [])]
    return [(k, str(v)) for k, v in out if v]


def _withholdable(k: str) -> bool:
    if k == "earnings_rationale" or k in SOFT_SCALAR_KEYS:
        return True
    if k.startswith(tuple(f"{x}[" for x in (*LIST_KEYS, *SOFT_LIST_KEYS))):
        return True
    return k.startswith(("scenarios_bbb[", "catalysts[", "horizons.", "style."))


def _scrub_bad(obj: Dict[str, Any], bad: set) -> None:
    """Remove withheld narrative items in place: scalars blanked, list items dropped."""
    for k in ("earnings_rationale", *SOFT_SCALAR_KEYS):
        if k in bad:
            obj[k] = ""
    for key in (*LIST_KEYS, *SOFT_LIST_KEYS):
        if isinstance(obj.get(key), list):
            obj[key] = [t for i, t in enumerate(obj[key]) if f"{key}[{i}]" not in bad]
    if isinstance(obj.get("style"), dict) and "style.reason" in bad:
        obj["style"]["reason"] = ""
    for i, s in enumerate(obj.get("scenarios_bbb") or []):
        if isinstance(s, dict):
            for field in ("drivers", "assumptions"):
                if isinstance(s.get(field), list):
                    s[field] = [t for j, t in enumerate(s[field]) if f"scenarios_bbb[{i}].{field}[{j}]" not in bad]
    for i, c in enumerate(obj.get("catalysts") or []):
        if isinstance(c, dict):
            for f in ("event", "if_good", "if_bad"):
                if f"catalysts[{i}].{f}" in bad:
                    c[f] = ""
    for k, v in (obj.get("horizons") or {}).items():
        if isinstance(v, dict) and f"horizons.{k}.why" in bad:
            v["why"] = ""


def check(obj: Optional[Dict[str, Any]], *, side: str, reg: Dict[str, Dict[str, Any]], atr: Optional[float],
          em_pct: float, forming: bool, state: str, rsi2_ready: bool, earnings_days: Optional[int],
          earnings_date: Optional[str], block_days: int, today: date, sections: List[Tuple[str, str]],
          dw: Dict[str, Any], live: Optional[float] = None,
          quant: Optional[Dict[str, Any]] = None,
          tape: Optional[Dict[str, Any]] = None,
          manage: bool = False, has_entry_plan: bool = True) -> Tuple[Dict[str, Any], List[str]]:
    """Resolve level names to prices and assert the object. Returns (resolved, failures); nothing is modified."""
    from src.logic.scenario_plans import _rr_floor, normalize
    from src.tracking.trade_rules import STOP_ATR_MIN, T1_MAX_EXPECTED_MOVE_MULT

    fails: List[str] = []
    if not isinstance(obj, dict):
        return {}, ["writer emitted no parseable JSON object"]
    if not atr:
        return {}, ["ATR14 unavailable: stop width cannot be asserted"]
    floor = _rr_floor()
    close = reg.get("CLOSE", {}).get("price")
    if str(obj.get("side") or "").upper() != side:
        fails.append(f"side {obj.get('side')!r} != document side {side}")
    verdict = str(obj.get("verdict") or "").strip().upper()
    veto = str(obj.get("veto") or "").strip().upper()
    stance = str(obj.get("earnings_stance") or "").strip().upper()
    allowed = VERDICTS if not manage else (MANAGE_VERDICTS + VERDICTS if has_entry_plan else MANAGE_VERDICTS)
    manage_v = manage and verdict in MANAGE_VERDICTS
    if verdict not in allowed:
        fails.append(f"verdict {obj.get('verdict')!r} not in {allowed}")
    if veto not in VETOES:
        fails.append(f"veto {obj.get('veto')!r} not in {VETOES}")
    if stance not in STANCES:
        fails.append(f"earnings_stance {obj.get('earnings_stance')!r} not in {STANCES}")
    traces: List[Dict[str, Any]] = []
    withheld: List[str] = []
    bad: set = set()
    for k, v in _prose_fields(obj):
        rows, why = trace(k, v, sections, dw, reg)
        if why and _withholdable(k):
            withheld += why
            bad.add(k)
            continue
        traces += rows
        fails += why
    _scrub_bad(obj, bad)
    if veto != "NONE" and not str(obj.get("veto_fact") or "").strip():
        fails.append(f"veto {veto} without veto_fact")
    if veto == "HARD_FUNDAMENTAL" and (verdict != "CASH_SKIP" or obj.get("scenarios")) and not manage_v:
        fails.append("HARD_FUNDAMENTAL requires CASH_SKIP and no scenarios")
    if verdict == "WATCH (EVENT RISK)" and veto != "EVENT_RISK":
        fails.append("WATCH (EVENT RISK) requires veto EVENT_RISK")
    if earnings_days is not None and 0 <= earnings_days <= block_days and verdict not in ("WATCH (EVENT RISK)", "CASH_SKIP") and not manage_v:
        fails.append(f"earnings in {earnings_days} trading days (block {block_days}) but verdict {verdict}")
    if earnings_days is not None and 0 <= earnings_days <= STANCE_WINDOW_DAYS and stance in ("", "NONE"):
        fails.append(f"earnings in {earnings_days} trading days but earnings_stance NONE")

    entry = obj.get("entry") if isinstance(obj.get("entry"), dict) else {}
    kind = str(entry.get("kind") or "NONE").upper()
    if kind not in ENTRY_KINDS:
        fails.append(f"entry.kind {entry.get('kind')!r} not in {ENTRY_KINDS}")
    has_plan = kind != "NONE"
    if verdict == "CASH_SKIP" and has_plan:
        fails.append(f"CASH_SKIP with entry kind {kind}")
    res: Dict[str, Any] = {"verdict": verdict, "veto": veto, "stance": stance, "entry_kind": kind, "levels": {},
                           "traces": traces, "withheld": withheld}
    L = res["levels"]
    if kind in ("ZONE", "LIMIT"):
        L["entry_low"] = _level(reg, entry.get("low") if kind == "ZONE" else (entry.get("low") or entry.get("high")), "entry.low", fails, True)
        L["entry_high"] = _level(reg, entry.get("high") or (entry.get("low") if kind == "LIMIT" else None), "entry.high", fails, True)
    elif kind == "BREAKOUT":
        L["trigger"] = _level(reg, entry.get("trigger"), "entry.trigger", fails, True)
    elif kind == "NEXT_OPEN":
        L["entry_high"] = _level(reg, entry.get("high") or "RSI2_CEILING", "entry.high", fails, True)
    L["stop"] = _level(reg, obj.get("stop"), "stop", fails, has_plan or manage_v)
    L["target_1"] = _level(reg, obj.get("target_1"), "target_1", fails, has_plan or manage_v)
    L["target_2"] = _level(reg, obj.get("target_2"), "target_2", fails, False)
    p = {k: (v or {}).get("price") for k, v in L.items()}
    plan_stop_name = (L.get("stop") or {}).get("level")
    bad_stop = _stop_ref_bad(str(obj.get("vs_alternatives") or ""), plan_stop_name, reg)
    if bad_stop:
        obj["vs_alternatives"] = ""
        withheld.append(f"vs_alternatives withheld (cites {level_label(bad_stop)} but the plan stop is "
                        f"{level_label(plan_stop_name) if plan_stop_name else 'none'})")

    if kind == "NEXT_OPEN":
        if not rsi2_ready:
            fails.append("NEXT_OPEN without a pending RSI2 order")
        for k, want in (("entry_high", "RSI2_CEILING"), ("stop", "RSI2_STOP"), ("target_1", "RSI2_TARGET")):
            if (L.get(k) or {}).get("level") != want:
                fails.append(f"NEXT_OPEN {k} must be {want}")
    fill_low = p.get("entry_low") or p.get("trigger") or p.get("entry_high")
    fill_high = p.get("entry_high") or p.get("trigger")
    if has_plan and p.get("stop") and p.get("target_1") and fill_low and fill_high:
        if not p["stop"] < fill_low <= fill_high < p["target_1"]:
            fails.append(f"inverted geometry: stop {p['stop']} / entry {fill_low}-{fill_high} / target_1 {p['target_1']}")
        if p.get("target_2") and not p["target_2"] > p["target_1"]:
            fails.append(f"target_2 {p['target_2']} not above target_1 {p['target_1']}")
        if atr and kind != "NEXT_OPEN" and p["stop"] > round(fill_low - STOP_ATR_MIN * atr, 2) + PRICE_TOL:
            fails.append(f"stop {p['stop']} tighter than {STOP_ATR_MIN:g} ATR below the lowest fill {fill_low}")
        if fill_high > p["stop"] and kind != "NEXT_OPEN":
            rr = (p["target_1"] - fill_high) / (fill_high - p["stop"])
            res["rr_at_entry"] = round(rr, 2)
            if rr < floor - 1e-9:
                fails.append(f"R:R {rr:.2f} at the entry high below the floor {floor:g}")
        if close:
            if kind in ("ZONE", "LIMIT") and fill_low > close + PRICE_TOL:
                fails.append(f"{kind} entry low {fill_low} above close {close}")
            if kind == "BREAKOUT" and fill_low <= close:
                fails.append(f"BREAKOUT trigger {fill_low} not above close {close}")
            if p["stop"] >= close:
                fails.append(f"stop {p['stop']} at or above close {close}")
            if close > p["stop"]:
                res["rr_at_market"] = round((p["target_1"] - close) / (close - p["stop"]), 2)
        if live and not p["stop"] < live < p["target_1"]:
            fails.append(f"live quote {live} outside stop {p['stop']} / target_1 {p['target_1']}: the bar's levels are stale")
        elif live and kind in ("ZONE", "LIMIT") and live < fill_low - PRICE_TOL:
            fails.append(f"live quote {live} below the entry {fill_low}: the limit is no longer resting, the bar's levels are stale")
        pine_t = (L.get("target_1") or {}).get("level") in ("PINE_TARGET", "RSI2_TARGET")
        if not pine_t and (em_pct or 0) > 0 and close and p["target_1"] > close * (1 + T1_MAX_EXPECTED_MOVE_MULT * em_pct / 100) + PRICE_TOL:
            fails.append(f"target_1 {p['target_1']} beyond {T1_MAX_EXPECTED_MOVE_MULT:g}x the 21-bar expected move")

    if manage_v:
        if kind != "NONE":
            fails.append(f"{verdict} with entry kind {kind}: a manage verdict carries no new entry")
        trim = str(obj.get("trim") or "NONE").strip().upper()
        res["trim"] = trim
        if trim not in TRIM_AMOUNTS:
            fails.append(f"trim {obj.get('trim')!r} not in {TRIM_AMOUNTS}")
        elif verdict == "HOLD" and trim != "NONE":
            fails.append(f"HOLD with trim {trim}")
        elif verdict == "TRIM" and trim not in ("THIRD", "HALF"):
            fails.append("TRIM needs a partial amount (THIRD or HALF); ALL is an EXIT" if trim == "ALL"
                         else "TRIM without a trim amount")
        elif verdict == "EXIT" and trim == "NONE":
            fails.append("EXIT without a trim amount")
        if verdict == "EXIT" and not (_CITE.search(str(obj.get("veto_fact") or "")) or _CITE.search(str(obj.get("wrong_if") or ""))):
            fails.append("EXIT without a cited reason (veto_fact or wrong_if)")
        if close and p.get("stop"):
            if p["stop"] >= close:
                fails.append(f"stop {p['stop']} at or above close {close}")
            elif p["stop"] > round(close - STOP_ATR_MIN * atr, 2) + PRICE_TOL:
                fails.append(f"stop {p['stop']} tighter than {STOP_ATR_MIN:g} ATR below the close {close}")
        if close and p.get("target_1") and p["target_1"] <= close:
            fails.append(f"target_1 {p['target_1']} not above close {close}")
        if p.get("target_2") and p.get("target_1") and p["target_2"] <= p["target_1"]:
            fails.append(f"target_2 {p['target_2']} not above target_1 {p['target_1']}")
        if (em_pct or 0) > 0 and close and p.get("target_1") and p["target_1"] > close * (1 + T1_MAX_EXPECTED_MOVE_MULT * em_pct / 100) + PRICE_TOL:
            fails.append(f"target_1 {p['target_1']} beyond {T1_MAX_EXPECTED_MOVE_MULT:g}x the 21-bar expected move")
        if live and p.get("stop") and p.get("target_1") and not p["stop"] < live < p["target_1"]:
            fails.append(f"live quote {live} outside stop {p['stop']} / target_1 {p['target_1']}: the bar's levels are stale")

    if verdict == "ENTER":
        if kind not in ("ZONE", "LIMIT", "NEXT_OPEN"):
            fails.append(f"ENTER with entry kind {kind}")
        if forming:
            fails.append("ENTER on a forming (not closed) daily bar")
        if veto != "NONE":
            fails.append(f"ENTER with veto {veto}")
        if kind == "NEXT_OPEN":
            if earnings_days is not None and 0 <= earnings_days <= RSI2_HOLD_DAYS:
                fails.append(f"RSI2 ENTER with earnings in {earnings_days} trading days (hold {RSI2_HOLD_DAYS})")
        elif state != "ACTIONABLE":
            fails.append(f"ENTER while Pine state is {state}")
        elif res.get("rr_at_market") is None or res["rr_at_market"] < floor - 1e-9:
            fails.append(f"ENTER with R:R at market {res.get('rr_at_market')} below the floor {floor:g}")
        elif close and not (p.get("entry_low") or 0) - PRICE_TOL <= close <= (p.get("entry_high") or 0) + PRICE_TOL:
            fails.append(f"ENTER with close {close} outside the entry {p.get('entry_low')}-{p.get('entry_high')}")
    if verdict == "ARMED" and not obj.get("scenarios"):
        resting = kind in ("ZONE", "LIMIT") and (p.get("entry_high") or 0) < (close or 0)
        if not (resting or kind == "BREAKOUT"):
            fails.append("ARMED with nothing armed (no resting entry below close, no breakout, no scenario)")

    res["scenarios"] = []
    main_fails, fails = fails, []
    for i, s in enumerate(obj.get("scenarios") or []):
        if not isinstance(s, dict):
            fails.append(f"scenarios[{i}]: not an object")
            continue
        sk = str(s.get("kind") or "").upper()
        if sk not in SCENARIO_KINDS:
            fails.append(f"scenarios[{i}].kind {s.get('kind')!r} not in {SCENARIO_KINDS}")
            continue
        sl = {k: _level(reg, s.get(k), f"scenarios[{i}].{k}", fails, k != "zone_low") for k in ("trigger", "zone_low", "stop", "target")}
        if any(sl[k] is None for k in ("trigger", "stop", "target")):
            continue
        if sk == "PULLBACK":
            if sl["zone_low"] is None:
                fails.append(f"scenarios[{i}]: PULLBACK needs zone_low")
                continue
            if atr and not sl["trigger"]["price"] - 1.5 * atr < sl["zone_low"]["price"] < sl["trigger"]["price"]:
                fails.append(f"scenarios[{i}]: zone_low not within one and a half ATR below the trigger")
        try:
            end = date.fromisoformat(str(s.get("window_end"))[:10])
            if end <= today:
                fails.append(f"scenarios[{i}]: window_end {end} not after {today}")
        except ValueError:
            fails.append(f"scenarios[{i}]: window_end {s.get('window_end')!r} not an ISO date")
        flat = {"kind": sk, "trigger": sl["trigger"]["price"], "stop": sl["stop"]["price"], "target": sl["target"]["price"],
                "zone_low": (sl["zone_low"] or {}).get("price"), "window_end": s.get("window_end"),
                "confirm": s.get("confirm") or [], "thesis": s.get("thesis") or "", "origin": "contract",
                "provenance": {k: v["level"] for k, v in sl.items() if v}}
        sc, why = normalize(flat, close or 0, atr or 0, em_pct, today, earnings_date,
                            (reg.get("PINE_STOP") or {}).get("price") or 0.0)
        if not sc:
            fails.append(f"scenarios[{i}]: {why}")
            continue
        drift = [k for k in ("trigger", "stop", "target", "zone_low")
                 if flat.get(k) and abs(float(sc[k]) - float(flat[k])) > PRICE_TOL]
        if sc.get("notes") or drift:
            fails.append(f"scenarios[{i}]: would need repair ({'; '.join(sc.get('notes') or []) or ', '.join(drift)})")
            continue
        sc["provenance"] = flat["provenance"]
        res["scenarios"].append(sc)
    withheld += [f"scenario dropped ({f})" for f in fails]
    fails = main_fails
    ref = live or close
    res["scenarios_bbb"] = _check_bbb(obj, reg, fails, withheld, ref, today, quant,
                                    (L.get("stop") or {}).get("level"))
    res["catalysts"] = _check_catalysts(obj, withheld, sections)
    tx_drops: List[str] = []
    res["transmission"] = _check_transmission(obj, tx_drops, sections, dw, reg, tape)
    res["transmission_withheld"] = tx_drops
    res["horizons"] = _check_horizons(obj, withheld)
    res["style"] = _check_style(obj, fails)
    return res, main_fails


def _check_bbb(obj: Dict[str, Any], reg: Dict[str, Dict[str, Any]], fails: List[str],
               withheld: List[str], ref: Optional[float], today: date,
               quant: Optional[Dict[str, Any]], stop_level: Optional[str] = None) -> List[Dict[str, Any]]:
    """Bull/base/bear scenarios: level names resolved by code, pct + base-rate odds filled by code."""
    from src.logic.scenario_plans import _sessions_between
    from src.logic.deep_research import quant_facts as _qf

    out: List[Dict[str, Any]] = []
    seen = set()
    for i, s in enumerate(obj.get("scenarios_bbb") or []):
        if not isinstance(s, dict):
            withheld.append(f"scenarios_bbb[{i}] dropped (not an object)")
            continue
        case = str(s.get("case") or "").strip().upper()
        if case not in BBB_CASES or case in seen:
            withheld.append(f"scenarios_bbb[{i}] dropped (case {s.get('case')!r} missing or duplicated)")
            continue
        lv = _level(reg, s.get("level"), f"scenarios_bbb[{i}].level", fails, True)
        if lv is None:
            continue
        by = str(s.get("by") or "")[:10]
        try:
            if date.fromisoformat(by) <= today:
                withheld.append(f"scenarios_bbb[{i}] dropped (by {by} not in the future)")
                continue
        except ValueError:
            withheld.append(f"scenarios_bbb[{i}] dropped (by {s.get('by')!r} not an ISO date)")
            continue
        seen.add(case)
        out.append({"case": case, "level": lv["level"], "price": lv["price"], "by": by,
                    "drivers": s.get("drivers") or [], "assumptions": s.get("assumptions") or []})
    if ref:
        for s in list(out):
            if (s["case"] == "BULL" and s["price"] <= ref) or (s["case"] == "BEAR" and s["price"] >= ref):
                withheld.append(f"scenarios_bbb {s['case']} {s['price']} on the wrong side of {ref}: dropped")
                out.remove(s)
    d = {s["case"]: s for s in out}
    base, bear, bull = d.get("BASE"), d.get("BEAR"), d.get("BULL")
    if base and ((bear and base["price"] <= bear["price"]) or (bull and base["price"] >= bull["price"])):
        withheld.append(f"scenarios_bbb BASE {base['price']} not between bear and bull: dropped")
        out.remove(base)
    for s in out:
        if ref:
            s["pct_from_ref"] = round(100 * (s["price"] / ref - 1), 1)
        try:
            days = _sessions_between(today, date.fromisoformat(s["by"]))
            odds = _qf.touch_odds_for(quant, s["level"], days) if quant else None
            s["odds"] = ({**odds, "kind": "base_rate"} if isinstance(odds, dict) else "not estimated")
        except Exception:
            s["odds"] = "not estimated"
    for s in out:
        if s["case"] == "BEAR" and s["level"] != stop_level:
            s["role"] = "reference"
    return out


def _check_catalysts(obj: Dict[str, Any], withheld: List[str],
                     sections: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Every catalyst date must be ISO and appear in a cited source (or any source when uncited)."""
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(obj.get("catalysts") or []):
        if not isinstance(c, dict):
            withheld.append(f"catalysts[{i}] dropped (not an object)")
            continue
        d = str(c.get("date") or "")[:10]
        try:
            date.fromisoformat(d)
        except ValueError:
            withheld.append(f"catalysts[{i}] dropped (date {c.get('date')!r} not an ISO date)")
            continue
        found = None
        for cite in _CITE.findall(str(c.get("event") or "")):
            if cite.startswith("S") and cite[1:].isdigit() and int(cite[1:]) < len(sections) \
                    and d in str(sections[int(cite[1:])][1]):
                found = cite
                break
        if found is None and not any(d in str(body) for _, body in sections):
            withheld.append(f"catalysts[{i}] dropped (date {d} appears in no source)")
            continue
        out.append({"date": d, "event": str(c.get("event") or ""),
                    "if_good": str(c.get("if_good") or ""), "if_bad": str(c.get("if_bad") or "")})
    return out


_DIRECTIONS = ("HEADWIND", "TAILWIND", "MIXED")


def _check_transmission(obj: Dict[str, Any], drops: List[str], sections: List[Tuple[str, str]],
                        dw: Dict[str, Any], reg: Dict[str, Dict[str, Any]],
                        tape: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Writer-authored event -> channel -> group -> ticker chains. Soft check: a bad chain is
    recorded in ``drops`` (res["transmission_withheld"]) — never in ``withheld``, so a chain
    can neither fail the document nor trigger/score a writer retry. Context only."""
    from src.logic.deep_research.market_events import TAPE, tape_status
    from src.logic.deep_research.transmission_rules import CHANNELS

    out: List[Dict[str, Any]] = []
    for i, ch in enumerate(obj.get("transmission") or []):
        if len(out) >= 4:
            drops.append("transmission capped at 4 chains")
            break

        def drop(why: str) -> None:
            drops.append(f"transmission[{i}] dropped ({why})")

        if not isinstance(ch, dict):
            drop("not an object")
            continue
        event = str(ch.get("event") or "").strip()
        status = str(ch.get("status") or "").strip().upper()
        channel = str(ch.get("channel") or "").strip().upper()
        if not event:
            drop("no event")
            continue
        cites = [c for c in _CITE.findall(event)
                 if c.startswith("S") and c[1:].isdigit() and int(c[1:]) < len(sections)]
        if not cites:
            drop("event has no [S<n>] citation")
            continue
        if status not in ("VERIFIED", "SCENARIO"):
            drop(f"status {ch.get('status')!r} not VERIFIED|SCENARIO")
            continue
        if status == "SCENARIO" and not re.match(r"if\b", event, re.I):
            drop('SCENARIO event must be phrased "If ..."')
            continue
        event_date = next(iter(_DATE.findall(event)), None)
        if status == "VERIFIED":
            if not event_date or not any(event_date in str(sections[int(c[1:])][1]) for c in cites):
                drop("VERIFIED event needs a cited ISO date")
                continue
        if channel not in CHANNELS:
            drop(f"channel {ch.get('channel')!r} not a known channel")
            continue
        steps, bad = [], None
        for j, s in enumerate(ch.get("chain") or []):
            txt = str((s or {}).get("step") or "").strip() if isinstance(s, dict) else ""
            kind = str((s or {}).get("kind") or "").strip().upper() if isinstance(s, dict) else ""
            if not txt or kind not in ("FACT", "INFERENCE"):
                bad = f"chain[{j}] needs step text and kind FACT|INFERENCE"
                break
            if kind == "FACT" and not _CITE.findall(txt):
                bad = f"chain[{j}] FACT without a citation"
                break
            if kind == "INFERENCE" and numbers(txt):
                bad = f"chain[{j}] INFERENCE contains numbers"
                break
            steps.append({"step": txt, "kind": kind})
        if bad:
            drop(bad)
            continue
        direction = str(ch.get("direction") or "").strip().upper()
        horizon = str(ch.get("horizon") or "").strip().upper()
        if direction not in _DIRECTIONS:
            drop(f"direction {ch.get('direction')!r} not {'|'.join(_DIRECTIONS)}")
            continue
        if horizon not in HORIZON_KEYS:
            drop(f"horizon {ch.get('horizon')!r} not in HORIZON_KEYS")
            continue
        exposure = str(ch.get("exposure") or "").strip()
        if not exposure or not _CITE.findall(exposure):
            drop("exposure missing or uncited")
            continue
        # Confirm symbols: the channel's proxies, plus the ticker and its sector ETF —
        # they are already in the tape and are often the only meaningful confirmation.
        proxies = set(CHANNELS[channel]["proxies"])
        allowed = proxies | {str(s).upper() for s in (tape or {}).get("extras") or []}
        allowed |= {str(r.get("symbol") or "").upper() for r in (tape or {}).get("rows") or []} - set(TAPE)
        confirm, bad = [], None
        for c in ch.get("confirm") or []:
            sym = str((c or {}).get("symbol") or "").strip().upper() if isinstance(c, dict) else ""
            mv = str((c or {}).get("move") or "").strip().upper() if isinstance(c, dict) else ""
            if sym not in allowed:
                bad = f"confirm {sym or c!r} not a {channel} proxy"
                break
            if mv not in ("UP", "DOWN"):
                bad = f"confirm move {c.get('move')!r} not UP|DOWN"
                break
            confirm.append({"symbol": sym, "move": mv})
        if bad:
            drop(bad)
            continue
        fails = []
        invalidate = str(ch.get("invalidate") or "").strip()
        for field, txt in ([("event", event)] + [(f"chain[{j}]", s["step"]) for j, s in enumerate(steps)]
                           + [("exposure", exposure), ("invalidate", invalidate)]):
            _, why = trace(f"transmission.{field}", txt, sections, dw, reg)
            fails += why
        if fails:
            drop("; ".join(fails))
            continue
        tape_rows = {str(r.get("symbol") or "").upper(): r for r in (tape or {}).get("rows") or []}
        for c in confirm:
            c["actual"] = (tape_rows.get(c["symbol"]) or {}).get("d1")
        ts = tape_status(confirm, tape or {})
        as_of = str((tape or {}).get("as_of") or "")
        if status == "VERIFIED" and event_date and as_of and event_date > as_of:
            ts = "UNTESTED"   # the tape's last session predates the event — it cannot confirm it
        out.append({"event": event, "status": status, "channel": channel, "chain": steps,
                    "exposure": exposure, "direction": direction, "horizon": horizon,
                    "confirm": confirm, "invalidate": invalidate,
                    "tape_status": ts})
    return out


def _stop_ref_bad(text: str, plan_stop: Optional[str], reg: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Name of a stop-flavored level other than the plan's stop named in prose, else None.
    The plan's stop is the only 'stop'; the Pine and ATR-floor levels are reference levels."""
    for n in reg:
        if "STOP" not in n or n == plan_stop:
            continue
        if f"[L:{n}]" in text or re.search(rf"\b{re.escape(n)}\b", text) \
                or re.search(rf"\b{re.escape(level_label(n))}\b", text, re.I):
            return n
    return None


def _check_horizons(obj: Dict[str, Any], withheld: List[str]) -> Dict[str, Dict[str, str]]:
    """All six horizon keys present with per-horizon stances and distinct reasons."""
    hz = obj.get("horizons")
    if not isinstance(hz, dict):
        if hz is not None:
            withheld.append("horizons dropped (not an object)")
        hz = {}
    _alias = {"12M": "1Y", "12_MONTH": "1Y", "1_YEAR": "1Y"}
    hz = {_alias.get(str(k).strip().upper().replace("-", "_").replace(" ", "_"),
                     str(k).strip().upper().replace("-", "_").replace(" ", "_")): v
          for k, v in hz.items()}
    missing = [k for k in HORIZON_KEYS if not isinstance(hz.get(k), dict)]
    if missing:
        withheld.append(f"horizons missing {', '.join(missing)}")
    seen_why: set = set()
    out: Dict[str, Dict[str, str]] = {}
    for k in HORIZON_KEYS:
        v = hz.get(k)
        if not isinstance(v, dict):
            out[k] = {"stance": "NOT_ASSESSED", "why": ""}
            continue
        st = str(v.get("stance") or "").strip().upper()
        if st not in HORIZON_STANCES:
            withheld.append(f"horizons.{k}: stance {v.get('stance')!r} not in {HORIZON_STANCES}: NOT_ASSESSED")
            st = "NOT_ASSESSED"
        why = str(v.get("why") or "").strip()
        norm = re.sub(r"\W+", " ", why.lower()).strip()
        if why and norm in seen_why:
            withheld.append(f"horizons.{k}: duplicate reason removed")
            why = ""
        if norm:
            seen_why.add(norm)
        out[k] = {"stance": st, "why": why}
    return out


def _check_style(obj: Dict[str, Any], fails: List[str]) -> Dict[str, str]:
    st = obj.get("style")
    if not isinstance(st, dict):
        return {}
    best = str(st.get("best_fit") or "").strip().upper()
    if best and best not in STYLE_FITS:
        fails.append(f"style.best_fit {st.get('best_fit')!r} not in {STYLE_FITS} (no day-trade style)")
    return {"best_fit": best if best in STYLE_FITS else "",
            "reason": str(st.get("reason") or "")}


def decision_label(watch: Dict[str, Any], res: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """Code picks the trader-facing label; the writer never does, so it cannot contradict the evidence.
    The short side is shown as levels only — no measured edge — and is never the verdict."""
    lv = res.get("levels") or {}
    p = lambda k: (lv.get(k) or {}).get("price")
    lo, hi, stop, t1 = p("entry_low"), p("entry_high"), p("stop"), p("target_1")
    live, close = _f(facts.get("live")), _f(facts.get("close"))
    px = live or close
    verdict = str(res.get("verdict") or watch.get("verdict") or "CASH_SKIP").upper()
    veto = str(res.get("veto") or watch.get("veto") or "NONE").upper()
    kind = str(res.get("entry_kind") or "").upper()
    fails = list(watch.get("contract_failures") or [])
    held = (str(watch.get("kind") or "").upper() == "MANAGE" or _has_position(facts.get("position"))
            or bool((facts.get("position_obj") or {}).get("has_position")))

    def d(code: str, label: str, why: List[str]) -> Dict[str, Any]:
        return {"code": code, "label": label, "why": why}

    if held:
        if live and stop and live <= stop:
            return d("SELL", "SELL", [f"Invalidation hit: live ${live:.2f} traded at or below the stop ${stop:.2f}."])
        if live and t1 and live >= t1:
            return d("SELL", "SELL", [f"Live ${live:.2f} reached target 1 (${t1:.2f}); the measured move is spent."])
        if fails:
            return d("HOLD", "HOLD (plan rejected)",
                     [f"The manage review failed its checks ({fails[0]}); the position stays held and no new levels are armed."])
        if veto == "HARD_FUNDAMENTAL":
            return d("EXIT", "EXIT", ["Hard fundamental veto on a held name."])
        if verdict == "EXIT":
            return d("EXIT", "EXIT", ["The manage review says exit the position; the price has not hit the stop or the target yet."])
        if verdict == "TRIM":
            amt = str(res.get("trim") or "").upper()
            return d("TRIM", "TRIM", [f"The manage review says trim{f' {amt.lower()}' if amt in TRIM_AMOUNTS and amt != 'NONE' else ''} "
                                      f"at the targets; the stop ${stop:.2f} protects the rest." if stop else
                                      "The manage review says trim at the targets."])
        # ENTER/ARMED/WATCH on a held name are add plans: fall through to the normal label logic.
        if verdict in MANAGE_VERDICTS or verdict == "CASH_SKIP":
            return d("HOLD", "HOLD", ["Position open; neither the stop nor the target has been hit."])
    if live and stop and live <= stop:
        return d("AVOID", "AVOID", [f"Live ${live:.2f} is at or below the stop ${stop:.2f}; the plan is broken."])
    if live and t1 and live >= t1:
        return d("AVOID", "AVOID", [f"Live ${live:.2f} already reached target 1 (${t1:.2f}); the move this plan waited for has happened."])
    if veto == "HARD_FUNDAMENTAL":
        return d("AVOID", "AVOID", ["Hard fundamental veto."])
    if facts.get("fade"):
        return d("AVOID", "AVOID", ["The fade gate is active on this bar."])
    if verdict == "CASH_SKIP" or fails:
        return d("AVOID", "AVOID", ["Cash skip: " + (fails[0] if fails else "the contract produced no plan.")])
    if verdict == "ENTER" and veto == "NONE" and px:
        top = hi or lo
        rr = _rr(t1, px, stop)
        if top and px <= top + PRICE_TOL and rr is not None and rr >= 2:
            return d("BUY_NOW", "BUY NOW",
                     [f"Price ${px:.2f} is at or under the entry top ${top:.2f} with reward:risk {rr:.2f}."])
        if top and px > top:
            return d("BUY_ON_PULLBACK", "BUY ON PULLBACK",
                     [f"Live ${px:.2f} ran ahead of the zone top ${top:.2f}; the edge exists only on the pullback."])
        if rr is not None and rr < 2:
            return d("BUY_ON_PULLBACK", "BUY ON PULLBACK",
                     [f"Reward:risk {rr:.2f} at ${px:.2f} is below the floor; only the zone price carries the edge."])
        return d("BUY_NOW", "BUY NOW", [])
    if kind in ("ZONE", "LIMIT") and hi and px and px > hi:
        return d("BUY_ON_PULLBACK", "BUY ON PULLBACK",
                 [f"Live ${px:.2f} is above the armed zone top ${hi:.2f}."])
    if kind == "BREAKOUT" or verdict.startswith("WATCH") or veto == "EVENT_RISK" or verdict == "ARMED":
        return d("WAIT", "WAIT FOR CONFIRMATION",
                 [f"Nothing is triggered yet ({verdict}); the armed levels fire the alert."])
    return d("AVOID", "AVOID", [])


LEVEL_LABELS = {
    "PINE_ZONE_LOW": "Pine zone low", "PINE_ZONE_HIGH": "Pine zone high", "PINE_STOP": "Pine stop", "PINE_TARGET": "Pine target",
    "ATR_FLOOR_STOP": "ATR-floor stop", "RR_FLOOR_LIMIT": "R:R-floor entry limit", "VP_VAL": "value-area low",
    "VP_VAH": "value-area high", "VP_POC": "volume point of control", "DARVAS_BOX_TOP": "Darvas box top",
    "OPT_GAMMA_FLIP": "gamma flip", "CLOSE": "close",
    "VP_HVN_ABOVE": "volume node above", "VP_HVN_BELOW": "volume node below",
    "OPT_CALL_WALL": "call wall", "OPT_PUT_WALL": "put wall",
    "CONF_FLOOR_14D": "conformal floor", "CONF_FLOOR_21D": "conformal floor",
    "CONF_CEILING_21D": "conformal ceiling", "CONF_STOP_14D": "conformal stop",
}
_CITE_GROUP = re.compile(r"(?:\s*\[(?:S\d+|DW:[^\]]+|L:[A-Z0-9_]+)\])+")
_NAME_TAG = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\s*\[L:\1\]")
_BARE_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def level_label(name: str) -> str:
    if name in LEVEL_LABELS:
        return LEVEL_LABELS[name]
    m = re.fullmatch(r"KEY_(SUPPORT|RESISTANCE)_(\d+)", name)
    if m:
        return f"key {m.group(1).lower()} {m.group(2)}"
    m = re.fullmatch(r"HTF_(WEEKLY|MONTHLY)_(\d+)_(EMA|SMA)", name)
    if m:
        return f"{m.group(1).lower()} {m.group(2)}-{m.group(3)}"
    m = re.fullmatch(r"GAP_(UP|DOWN)_\d+_(LOW|HIGH)", name)
    if m:
        return f"gap {m.group(2).lower()}"
    m = re.fullmatch(r"SWING_(HIGH|LOW)_(\d+)", name)
    if m:
        return f"{m.group(2)}-bar {m.group(1).lower()}"
    return name.lower().replace("_", " ")


def _section_label(title: str) -> str:
    """Short human name for a SOURCES section; the deterministic earnings calendar is the
    'Earnings packet' even when the writer reached it through a fetch_earnings_calendar tool call."""
    if title.startswith("Judge research (fetch_earnings_calendar"):
        return "Earnings packet"
    return re.split(r"\s*\(", title)[0].strip() or title


def humanize(text: str, reg: Dict[str, Dict[str, Any]], sections: List[Tuple[str, str]]) -> str:
    """Display only: level names become 'label $price' and [S<n>]/[DW:..]/[L:..] tags become '(Source name)'.
    Validation runs on the raw text before this."""
    def level(m: "re.Match[str]") -> str:
        n = m.group(1) if m.lastindex else m.group(0)
        return f"{level_label(n)} ${reg[n]['price']:.2f}" if n in reg else m.group(0)

    def cite(m: "re.Match[str]") -> str:
        names: List[str] = []
        for tok in re.findall(r"\[([^\]]+)\]", m.group(0)):
            if tok.startswith("S") and tok[1:].isdigit() and int(tok[1:]) < len(sections):
                n = _section_label(sections[int(tok[1:])][0])
            elif tok.startswith("DW:"):
                n = "Data Window"
            else:
                continue
            if n not in names:
                names.append(n)
        return f" ({', '.join(names)})" if names else ""

    text = _NAME_TAG.sub(lambda m: level(m) if m.group(1) in reg else m.group(0), text)
    text = _CITE_GROUP.sub(cite, text)
    return _BARE_NAME.sub(lambda m: level(m) if m.group(0) in reg else m.group(0), text)


def _fmt(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


VERDICT_WORDS = {"ENTER (Next Open)": "BUY at the next open", "ENTER (Limit @ Floor)": "BUY with a limit order",
                 "ENTER": "BUY", "STALK": "STALK: wait for price to reach the levels", "ARMED": "STALK: wait for price to reach the levels",
                 "WATCH": "WATCH: no order yet", "WATCH (EVENT RISK)": "WATCH: event risk inside the hold", "CASH_SKIP": "NO TRADE",
                 "HOLD": "HOLD: keep the position", "TRIM": "TRIM: sell part at the targets", "EXIT": "EXIT: close the position"}


def _rr(target: Optional[float], entry: Optional[float], stop: Optional[float]) -> Optional[float]:
    return (target - entry) / (entry - stop) if target and entry and stop and stop < entry < target else None


def _decision(watch: Dict[str, Any], res: Dict[str, Any], facts: Dict[str, Any], fails: List[str], h,
              dec: Dict[str, Any], pine_reg: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    from src.logic.scenario_plans import _rr_floor

    lv = res.get("levels") or {}
    p = lambda k: (lv.get(k) or {}).get("price")
    lo, hi, trig, stop, t1, t2 = p("entry_low"), p("entry_high"), p("trigger"), p("stop"), p("target_1"), p("target_2")
    close, live, atr, kind = _f(facts.get("close")), _f(facts.get("live")), _f(facts.get("atr")), res.get("entry_kind")
    why: List[str] = list(dec.get("why") or [])
    if watch.get("veto") not in (None, "NONE"):
        why.append(f"{str(watch['veto']).replace('_', ' ').capitalize()}: {h(watch.get('veto_fact'))}")
    for f in fails:
        if f.startswith("adversarial pass"):
            r = watch.get("adversary_reason")
            why.append("An independent reviewer rejected the plan" + (f": {h(r)}" if r else "."))
        elif not f.startswith("live quote"):
            why.append(f"Check failed: {h(f)}")
    out = [f"## 🧭 DECISION: {dec.get('label') or 'AVOID'}", ""]
    if why:
        out += ["**Why:**", *[f"- {w}" for w in why], ""]
    px = f"close ${_fmt(close)} on {facts.get('bar_date')}" + (" (bar still forming)" if facts.get("forming") else "")
    out.append(f"- **Price:** {px}" + (f" · live ${live:.2f}" if live else ""))
    top = hi or trig
    manage = str(watch.get("kind") or "").upper() == "MANAGE" and str(res.get("verdict") or "").upper() in MANAGE_VERDICTS
    if stop and t1:
        if fails:
            out.append("> **⚠️ LEVELS REJECTED — the levels below are context, not an order.**")
        if manage:
            out.append(f"- **Protective exit:** ${stop:.2f} — a daily close below exits the position")
            out.append(f"- **Trim levels:** ${t1:.2f}" + (f" then ${t2:.2f}" if t2 else ""))
            out.append(f"- **Invalidation:** a daily close below ${stop:.2f}")
        elif kind in ("ZONE", "LIMIT") and lo and hi:
            out.append(f"- **Buy zone:** ${lo:.2f} – ${hi:.2f} (limit order; do not chase)")
            pz = [((pine_reg or {}).get(k) or {}).get("price") for k in ("PINE_ZONE_LOW", "PINE_ZONE_HIGH", "PINE_STOP")]
            if all(pz) and (abs(pz[0] - lo) > 0.01 or abs(pz[1] - hi) > 0.01):
                out.append(f"- **Pine's own zone differs:** ${pz[0]:.2f} – ${pz[1]:.2f}, stop ${pz[2]:.2f}. This plan buys lower, at a structural level the judge chose.")
        elif kind == "BREAKOUT" and trig:
            out.append(f"- **Buy:** on a break above ${trig:.2f}")
        elif kind == "NEXT_OPEN" and hi:
            out.append(f"- **Buy:** at the next open, no higher than ${hi:.2f}")
        if not manage:
            fill = lo if kind in ("ZONE", "LIMIT") and lo else top
            out.append(f"- **Stop:** ${stop:.2f}" + (f" ({(fill - stop) / atr:.2f} ATR under the lowest fill ${fill:.2f})" if fill and atr else ""))
            out.append(f"- **Targets:** ${t1:.2f}" + (f" then ${t2:.2f}" if t2 else ""))
            rr = [(f"{x:.2f} {where}") for x, where in ((_rr(t1, top, stop), "filled at the entry"), (_rr(t1, close, stop), "at the close"),
                                                        (_rr(t1, live, stop), "at the live price")) if x is not None]
            if rr:
                out.append(f"- **Reward:risk to target 1:** {' · '.join(rr)}")
            rr_live = _rr(t1, live or close, stop)
            if kind in ("ZONE", "LIMIT") and rr_live is not None and rr_live < _rr_floor():
                out.append(f"- **Buying now is a chase** (reward:risk {rr_live:.2f}). The edge exists only if price pulls back to the buy zone.")
            out.append(f"- **Invalidation:** a daily close below ${stop:.2f}")
            refs = [f"{level_label(n)} ${(pine_reg or {})[n]['price']:.2f}" for n in ("PINE_STOP", "ATR_FLOOR_STOP")
                    if (pine_reg or {}).get(n) and abs((pine_reg or {})[n]["price"] - stop) > 0.005]
            if refs:
                out.append("- **Reference levels (not the plan's stop):** " + " · ".join(refs))
    stance = res.get("stance")
    ecal = (f"next print {facts['earnings_date']}"
            + (f", {facts['earnings_days']} trading days" if facts.get("earnings_days") is not None else "")
            + " (Earnings packet)") if facts.get("earnings_date") else ""
    if stance or ecal:
        rat = h((watch.get("_obj") or {}).get("earnings_rationale") or "")
        out.append("- **Earnings:** " + " — ".join(x for x in
                   (str(stance).replace("_", " ").lower() if stance else "", ecal, rat) if x))
    st = res.get("style") or {}
    if st.get("best_fit"):
        out.append(f"- **Style:** {str(st['best_fit']).replace('_', ' ').title()}"
                   + (f" — {h(st.get('reason'))}" if st.get("reason") else ""))
    out.append("- **Timeframe:** daily/weekly setups only — intraday trading is out of scope")
    return out + [""]


def _position_md(facts: Dict[str, Any]) -> List[str]:
    """'Your position' section: rendered from the structured broker position dict; the raw prompt
    block (with its directive text and broker-reported day P/L mislabelled as unrealized) never
    reaches the report."""
    pos = facts.get("position_obj")
    out: List[str] = []
    if isinstance(pos, dict) and pos.get("has_position"):
        qty = _f(pos.get("quantity"))
        basis = _f(pos.get("average_price"))
        accts = [(a, _f(a.get("quantity"))) for a in pos.get("accounts") or [] if _f(a.get("quantity"))]
        px = _f(facts.get("live")) or _f(pos.get("last_price")) or _f(pos.get("current_price")) or _f(facts.get("close"))
        if qty:
            out.append(f"- **Shares:** {qty:g} {str(pos.get('side') or 'LONG')}"
                       + (f" — " + "; ".join(f"{a.get('account')}: {q:g} @ ${(_f(a.get('average_price')) or 0):,.2f}"
                                             for a, q in accts)
                          if len(accts) > 1 else ""))
        if basis:
            out.append(f"- **Cost basis:** ${basis:,.2f}" + (f" · current ${px:,.2f}" if px else ""))
        if basis and px and qty:
            out.append(f"- **Unrealized P/L:** ${(px - basis) * qty:+,.2f} ({100 * (px - basis) / basis:+.1f}%)")
        # Schwab reports the real day move; the local PORTFOLIO fallback's day_pnl is P/L since
        # entry — identical to the unrealized line above, so it is only shown for broker data.
        day = _fnum(pos.get("day_pnl"))
        if day is not None and str(pos.get("source") or "").upper() != "PORTFOLIO":
            out.append(f"- **Day P/L:** ${day:+,.2f} (day move only — not unrealized)")
        legs = [f"{_f(o.get('quantity')):+g} {str(o.get('symbol') or '').strip()}" for o in pos.get("options") or []
                if _f(o.get("quantity"))]
        if legs:
            out.append(f"- **Option legs:** {'; '.join(legs)}")
    elif _has_position(facts.get("position")):
        out.append(str(facts["position"]).split("• MANDATORY")[0].strip())
    return ["", "## Your position", *out] if out else []


def render(ticker: str, date_str: str, side: Optional[str], watch: Dict[str, Any], res: Dict[str, Any],
           fails: List[str], adversary: str, facts: Dict[str, Any], obj: Optional[Dict[str, Any]],
           extra_md: str = "", display: Optional[Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str]]]] = None,
           quant: Optional[Dict[str, Any]] = None, source_links: Optional[List[Dict[str, Any]]] = None,
           include_transmission: bool = True) -> str:
    def h(t: Any) -> str:
        return html.escape(humanize(str(t), *display) if display else str(t), quote=False)

    earn = (f"Earnings reported {facts['earnings_reported']} (next date unknown)" if facts.get("earnings_reported")
            else f"Earnings {facts.get('earnings_date') or 'unknown'}"
            + (f" ({facts['earnings_days']} trading days away)" if facts.get("earnings_days") is not None else ""))
    bar = (f"- Bar {facts.get('bar_date')} ({'still forming' if facts.get('forming') else 'closed'}) · close {_fmt(facts.get('close'))}"
           + (f" · Pine state {facts.get('state_label')}" if facts.get("state_label") else ""))
    stats = (f"- ATR14 {_fmt(facts.get('atr'))} · expected 21-day move {_fmt(facts.get('em_pct'))}% · IV rank {_fmt(facts.get('iv_rank'), 0)} · "
             f"extension Z {_fmt(facts.get('ext_z'))} · action code {facts.get('action')}"
             + ("" if facts.get("forming") else f" · relative volume {_fmt(facts.get('rvol'))}"))
    if side is None:
        return "\n".join([f"# {ticker} | ⚖️ SENIOR PM ARBITRATION ({date_str})", "", "## 🧭 DECISION: NO TRADE", "",
                          "No long plan exists on this bar (Pine's stop and target do not bracket the close, and there is no RSI2 order). "
                          "Everything below is context, not an order.", "", "## Facts", bar, stats, f"- {earn}",
                          *_position_md(facts), *(["", extra_md] if extra_md else [])])
    o = obj or {}
    lv = res.get("levels") or {}
    q = quant if quant is not None else (facts.get("quant") or {})
    links = _dedupe_sources(source_links if source_links is not None else (facts.get("source_links") or []))
    dec = decision_label(watch, res, facts)
    lines = [f"# {ticker} | ⚖️ SENIOR PM ARBITRATION ({date_str}) · {side.lower()} side", "",
             *_decision({**watch, "_obj": o}, res, facts, fails, h, dec, display[0] if display else None)]

    if o.get("thesis") or o.get("why_now") or o.get("edge"):
        lines += ["## 💡 INVESTMENT THESIS", *[f"- {h(t)}" for t in (o.get("thesis") or [])]]
        if o.get("why_now"):
            lines.append(f"- **Why now:** {h(o['why_now'])}")
        if o.get("edge"):
            lines.append(f"- **The edge:** {h(o['edge'])}")
        lines.append("")

    bbb = res.get("scenarios_bbb") or []
    if bbb:
        lines += ["## 🎲 SCENARIOS", "", "| case | level | move | by | touch odds | drivers | assumptions |",
                  "|---|---|---|---|---|---|---|"]
        for s in bbb:
            odds = s.get("odds")
            odds_s = f"{odds['pct']}% (n={odds['n']})" if isinstance(odds, dict) else str(odds or "not estimated")
            pct = f"{s['pct_from_ref']:+.1f}%" if s.get("pct_from_ref") is not None else "—"
            dr = "; ".join(h(t) for t in s.get("drivers") or [])
            asm = "; ".join(h(t) for t in s.get("assumptions") or [])
            lbl = level_label(s["level"])
            if s.get("role") == "reference":
                lbl = f"reference · {lbl.replace(' stop', '')}"
            lines.append(f"| {s['case']} | ${s['price']:.2f} ({lbl}) | {pct} | {s['by']} | {odds_s} | {dr} | {asm} |")
        lines += ["", "_Touch odds: historical chance price trades at that level by that date. Each case is measured on its own, so they overlap and do not sum to 100%._",
                  "_Prices, moves and odds are code-computed; drivers and assumptions are the writer's narrative._",
                  "_Legend: verified feed = cited source · code-computed = deterministic check · base rate = empirical history (n shown) · scenario = writer assumption._", ""]

    hz = res.get("horizons") or {}
    qb = (q or {}).get("horizon_base") or {}
    if hz or qb:
        lines += ["## 🕐 HORIZONS", "", "| horizon | stance | base rate p10 / p50 / p90 | why |", "|---|---|---|---|"]
        for k in HORIZON_KEYS:
            v = hz.get(k) or {}
            b = qb.get(str(HORIZON_BARS[k])) if k in HORIZON_BARS else None
            rate = (f"{b['p10']:+.1f}% / {b['p50']:+.1f}% / {b['p90']:+.1f}% (n={b['n']})" if b and b.get("p50") is not None
                    else "—")
            lines.append(f"| {k.replace('_', ' ')} | {v.get('stance') or '—'} | {rate} | {h(v.get('why'))} |")
        lines.append("")

    cats = res.get("catalysts") or []
    if cats:
        lines += ["## 🗓️ CATALYSTS", "", "| date | event | if it breaks well | if it breaks badly |", "|---|---|---|---|"]
        for c in cats:
            lines.append(f"| {c.get('date')} | {h(c.get('event'))} | {h(c.get('if_good'))} | {h(c.get('if_bad'))} |")
        lines.append("")

    for key, title in (("fundamentals", "## 📊 FUNDAMENTALS"), ("technicals", "## 📈 TECHNICALS"),
                       ("market_context", "## 🌐 MARKET CONTEXT")):
        items = [h(t) for t in (o.get(key) or [])]
        if items:
            lines += [title, *[f"- {t}" for t in items], ""]
    if include_transmission:
        lines += _transmission_lines(res, facts, h)
    items = [h(t) for t in (o.get("news_synthesis") or [])]
    if items:
        lines += ["## 📰 NEWS", *[f"- {t}" for t in items], ""]
    if q:
        rs21 = (q.get("rs_spy") or {}).get("1M")
        qbits = [x for x in (
            f"HV20 {q['hv20']}%" if q.get("hv20") else None,
            f"beta {q['beta']}" if q.get("beta") is not None else None,
            f"max drawdown 1y {q['max_dd_1y']}%" if q.get("max_dd_1y") is not None else None,
            f"avg $ vol 20d ${q['dollar_vol_20d']:,.0f}" if q.get("dollar_vol_20d") else None,
            f"vs SPY 1M {rs21:+.1f}pp" if rs21 is not None else None) if x]
        if qbits:
            lines += ["- **Code quant:** " + " · ".join(qbits), ""]

    if o.get("risks") or o.get("wrong_if") or o.get("case_against"):
        lines += ["## ⚠️ RISKS & THE CASE AGAINST", *[f"- {h(t)}" for t in (o.get("risks") or [])]]
        if o.get("wrong_if"):
            lines.append(f"- **This thesis is wrong if:** {h(o['wrong_if'])}")
        lines += [f"- {h(t)}" for t in (o.get("case_against") or [])]
        lines.append("")

    if not fails and any(lv.values()):
        lines += _directives(watch, res, facts, lv)
    if o.get("vs_alternatives"):
        lines += [f"- **Versus doing nothing or holding SPY:** {h(o['vs_alternatives'])}", ""]
    if extra_md:
        lines += [extra_md, ""]

    ruling = [(t, [h(x) for x in v]) for t, v in (("Where both reports agree", o.get("concurrence")),
                                                    ("Where they disagree", o.get("conflicts"))) if v]
    lines += ["## ⚖️ THE JUDGE'S FINAL RULING"]
    for title, items in ruling:
        lines += [f"* **{title}:**", *[f"    * {t}" for t in items]]
    dec_why = "; ".join(dec.get("why") or [])
    lines += [f"* **Final ruling:** **{dec.get('label') or 'AVOID'}**" + (f" — {h(dec_why)}" if dec_why else "")]
    lines += [f"* writer proposed **{res.get('verdict') or '—'}**"
              + (f"; code rejected: {h(fails[0])}" if fails else "")
              + (f" · veto {watch.get('veto')}: {h(watch.get('veto_fact'))}" if watch.get("veto") not in (None, "NONE") else ""), ""]
    if links:
        lines += ["## 📚 SOURCES", ""]
        for i, c in enumerate(links, 1):
            t = html.escape(str(c.get("title") or _domain(c.get("url")) or c.get("url") or ""), quote=False)
            u = html.escape(str(c.get("url") or ""), quote=True)
            topic = f" — {html.escape(str(c.get('topic') or ''), quote=False)}".rstrip()
            lines.append(f"{i}. [{t}]({u}){topic}")
        lines.append("")
    lines += ["---", "", "## 🔒 Audit trail (how every number was checked)", "",
              f"Code checks {'passed' if not fails else 'failed'} · independent review {adversary.lower()}", "", bar, stats, f"- {earn}"]
    if any(lv.values()):
        lines += ["", "| plan field | level | price | source |", "|---|---|---|---|"]
        lines += [f"| {k.replace('_', ' ')} | {level_label(v['level'])} | {v['price']:.2f} | {v['source']} |" for k, v in lv.items() if v]
    lines += _position_md(facts)
    if res.get("traces") or res.get("withheld") or res.get("transmission_withheld"):
        lines += ["", "<details><summary>Number provenance and removed lines</summary>", ""]
        if res.get("traces"):
            lines += ["#### Where each number came from", "", "| field | number | source |", "|---|---|---|"]
            lines += [f"| {t['field']} | {t['number']} | {h(t['cite']).strip(' ()') or t['source']} |" for t in res["traces"]]
        if res.get("withheld"):
            lines += ["", "#### Lines removed (a number in them appears in no source)",
                      *[f"- {h(w)}" for w in res["withheld"]]]
        if res.get("transmission_withheld"):
            lines += ["", "#### Transmission chains dropped (context only — never affects the verdict)",
                      *[f"- {h(w)}" for w in res["transmission_withheld"]]]
        lines += ["", "</details>"]
    if fails:
        lines += ["", "### Failed checks (raw)", *[f"- {h(f)}" for f in fails]]
    return "\n".join(lines)


def _domain(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(str(url)).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _dedupe_sources(links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the first source per (title-or-url, topic). Keying on domain would collapse every
    grounded citation — they all redirect through vertexaisearch.cloud.google.com."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for c in links or []:
        key = (str(c.get("title") or c.get("url") or ""), str(c.get("topic") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _transmission_lines(res: Dict[str, Any], facts: Dict[str, Any], h: Any) -> List[str]:
    """EVENTS & TRANSMISSION: writer-authored chains plus the code-computed tape.
    Context only — the verdict, gates and adversary never see or use this section."""
    chains = res.get("transmission") or []
    tape = facts.get("market_tape") or {}
    rows = [r for r in tape.get("rows") or [] if r.get("d1") is not None]
    if not chains and not rows:
        return []
    lines = ["## 🔀 EVENTS & TRANSMISSION", "",
             "_Context only: these chains inform the read; they cannot change the verdict or the levels._", ""]
    for ch in chains:
        lines.append(f"**{h(ch['event'])}** — _{ch['status'].lower()} · {ch['channel'].lower().replace('_', ' ')}"
                     f" · {ch['direction'].lower()} · {ch['horizon'].replace('_', ' ')}_")
        for j, s in enumerate(ch.get("chain") or [], 1):
            lines.append(f"{j}. _{s['kind'].lower()}_ — {h(s['step'])}")
        lines.append(f"- **Exposure:** {h(ch['exposure'])}")
        if ch.get("confirm"):
            lines += ["", "| confirm | expected | actual (1d) |", "|---|---|---|"]
            for c in ch["confirm"]:
                act = f"{c['actual']:+.2f}%" if isinstance(c.get("actual"), (int, float)) else "—"
                lines.append(f"| {c['symbol']} | {c['move'].lower()} | {act} |")
            lines.append("")
        lines.append(f"- **Tape check:** {ch.get('tape_status', 'UNTESTED').title()}"
                     + (f" · **invalidates if:** {h(ch['invalidate'])}" if ch.get("invalidate") else ""))
        lines.append("")
    if rows:
        tape_s = " · ".join(f"{r['symbol']} {r['d1']:+.1f}%" for r in rows)
        ext = tape.get("extended")
        ext_s = (f" · {ext['symbol']} ext {ext['price']:,.2f} ({ext['pct']:+.1f}%)" if ext and ext.get("pct") is not None
                 else f" · {ext['symbol']} ext {ext['price']:,.2f}" if ext else "")
        lines.append(f"- **Market tape (1d, as of {tape.get('as_of') or 'unknown'}"
                     f"{' (partial — bar still forming)' if tape.get('partial') else ''}):** {tape_s}{ext_s}")
        lines.append("")
    return lines


def _directives(watch: Dict[str, Any], res: Dict[str, Any], facts: Dict[str, Any], lv: Dict[str, Any]) -> List[str]:
    sp = watch.get("shares_plan") or {}
    out = []
    if sp.get("recommended_shares"):
        out.append(f"* **Shares:** {sp['recommended_shares']} at the risk budget")
    menu = watch.get("options_menu") or {}
    tiers = [(t, menu.get(k)) for t, k in (("Tactical spread (30-50 DTE)", "tactical_spread"), ("LEAPS (6-12 months)", "leaps"),
                                           ("Income / CSP (20-50 DTE)", "income_or_csp"),
                                           ("Covered call (30-60 DTE)", "covered_call"))
             if (menu.get(k) or {}).get("structure") not in (None, "NONE")]
    out += [f"* **{t}:** {m.get('structure')} - {m.get('summary')}" for t, m in tiers]
    legs = menu.get("existing_legs") or []
    if legs:
        out.append(f"* **Your option legs (untouched):** {'; '.join(str(x) for x in legs)}")
    return ["## 🎯 FINAL ACTIONABLE DIRECTIVES", *out, ""] if out else []


def _facts(dw: Dict[str, Any], forming: bool, atr: Optional[float], state_label: str,
           earnings_days: Optional[int], earnings_date: Optional[str], position: str) -> Dict[str, Any]:
    return {"bar_date": dw.get("bar_date") or dw.get("time"), "forming": forming, "close": dw.get("close") or dw.get("Close"),
            "atr": atr, "em_pct": _f(dw.get("Exp Move Pct 21b")) or 0.0, "iv_rank": dw.get("Energy IV Rank Pct"),
            "ext_z": dw.get("Ext Z Self Relative"), "action": dw.get("Action Long Code"), "rvol": dw.get("RVOL Vs Avg"),
            "state_label": state_label, "earnings_days": earnings_days, "earnings_date": earnings_date,
            "position": position if _has_position(position) else ""}


def _write(paths: List[Path], text: str) -> None:
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def live_last(sources: List[Tuple[str, str]]) -> Optional[float]:
    for title, body in sources:
        m = re.search(r"Last:\s*\$?([\d.,]+)", str(body or "")) if "quote" in title.lower() else None
        if m:
            return _f(m.group(1).replace(",", ""))
    return None


def load_bars(csv_path: Optional[Path], n: int = 300) -> List[Dict[str, Any]]:
    import csv

    if not csv_path or not Path(csv_path).exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))[-n:]


CONTEXT_LEVELS = ("AVWAP_SUPPORT", "AVWAP_RESISTANCE", "VP_POC", "VP_VAL", "VP_VAH",
                  "DARVAS_BOX_TOP", "MA20", "MA50", "MA200", "EMA21")


def build_report_obj(ticker: str, date_str: str, side: Optional[str], watch: Dict[str, Any],
                     res: Dict[str, Any], fails: List[str], adversary: str, facts: Dict[str, Any],
                     obj: Optional[Dict[str, Any]], quant: Optional[Dict[str, Any]],
                     source_links: Optional[List[Dict[str, Any]]],
                     reg: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One structured object feeding both the markdown render and the UI Decision tab.
    Code-computed fields carry kind="computed"; writer text is HTML-escaped."""
    o = obj or {}
    q = quant or {}
    esc = lambda t: html.escape(str(t or ""), quote=False)
    lv = res.get("levels") or {}
    dec = decision_label(watch, res, facts)
    qb = q.get("horizon_base") or {}
    live = _f(facts.get("live"))
    stop = (lv.get("stop") or {}).get("price")
    t1 = (lv.get("target_1") or {}).get("price")
    hz = res.get("horizons") or {}
    plan_names = {v.get("level") for v in lv.values() if isinstance(v, dict)}
    ctx_lv = {k: {"name": k, "label": level_label(k), "price": v["price"], "source": v.get("source"),
                  "kind": "computed"}
              for k, v in (reg or {}).items()
              if isinstance(v, dict) and isinstance(v.get("price"), (int, float))
              and (k in CONTEXT_LEVELS or k.startswith("KEY_SUPPORT_") or k.startswith("KEY_RESISTANCE_"))
              and k not in plan_names}
    return {
        "ticker": ticker, "date": date_str, "side": side, "engine": "contract", "kind": "computed",
        "decision": {"code": dec["code"], "label": dec["label"], "why": [esc(w) for w in dec.get("why") or []]},
        "style": res.get("style") or {},
        "verdict": res.get("verdict"), "veto": res.get("veto"), "veto_fact": esc(o.get("veto_fact")),
        "as_of": {"close": _f(facts.get("close")), "close_date": facts.get("bar_date"),
                  "bar_forming": bool(facts.get("forming")), "live": live,
                  "generated_at": datetime.now().isoformat(timespec="seconds")},
        "levels": {k: {"name": v["level"], "price": v["price"], "source": v["source"], "kind": "computed"}
                   for k, v in lv.items() if v},
        "context_levels": ctx_lv,
        "plan": watch.get("shares_plan") or {},
        "options_menu": watch.get("options_menu") or {},
        "thesis": [esc(t) for t in o.get("thesis") or []],
        "why_now": esc(o.get("why_now")), "edge": esc(o.get("edge")),
        "scenarios_bbb": [{"kind": "scenario",
                           **{kk: s.get(kk) for kk in ("case", "level", "price", "by", "pct_from_ref", "odds")},
                           "drivers": [esc(t) for t in s.get("drivers") or []],
                           "assumptions": [esc(t) for t in s.get("assumptions") or []]}
                          for s in res.get("scenarios_bbb") or []],
        "scenarios": res.get("scenarios") or [],
        "horizons": {k: {**(hz.get(k) or {"stance": "NOT_ASSESSED"}),
                         "why": esc((hz.get(k) or {}).get("why")),
                         "base": ({**qb[str(HORIZON_BARS[k])], "kind": "base_rate"}
                                  if k in HORIZON_BARS and isinstance(qb.get(str(HORIZON_BARS[k])), dict)
                                  and qb[str(HORIZON_BARS[k])].get("p50") is not None else None)}
                     for k in HORIZON_KEYS},
        "catalysts": [{"date": c.get("date"), "event": esc(c.get("event")),
                       "if_good": esc(c.get("if_good")), "if_bad": esc(c.get("if_bad"))}
                      for c in res.get("catalysts") or []],
        "fundamentals": [esc(t) for t in o.get("fundamentals") or []],
        "technicals": [esc(t) for t in o.get("technicals") or []],
        "market_context": [esc(t) for t in o.get("market_context") or []],
        "news": [esc(t) for t in o.get("news_synthesis") or []],
        "risks": [esc(t) for t in o.get("risks") or []],
        "case_against": [esc(t) for t in o.get("case_against") or []],
        "wrong_if": esc(o.get("wrong_if")), "vs_alternatives": esc(o.get("vs_alternatives")),
        "concurrence": [esc(t) for t in o.get("concurrence") or []],
        "conflicts": [esc(t) for t in o.get("conflicts") or []],
        "earnings": {"date": facts.get("earnings_date"), "days": facts.get("earnings_days"),
                     "stance": res.get("stance"), "rationale": esc(o.get("earnings_rationale")),
                     "median_move": q.get("earnings_move_median")},
        "transmission": [{"event": esc(ch.get("event")), "status": ch.get("status"),
                          "channel": ch.get("channel"), "direction": ch.get("direction"),
                          "horizon": ch.get("horizon"), "exposure": esc(ch.get("exposure")),
                          "invalidate": esc(ch.get("invalidate")), "tape_status": ch.get("tape_status"),
                          "chain": [{"step": esc(s.get("step")), "kind": s.get("kind")}
                                    for s in ch.get("chain") or []],
                          "confirm": [{"symbol": c.get("symbol"), "move": c.get("move"),
                                       "actual": c.get("actual")} for c in ch.get("confirm") or []]}
                         for ch in res.get("transmission") or []],
        "market_tape": facts.get("market_tape") or {},
        "quant": q, "quant_kind": "computed",
        "rr": {"at_entry": res.get("rr_at_entry"), "at_market": res.get("rr_at_market"), "at_live": _rr(t1, live, stop)},
        "position": (facts.get("position_obj") if isinstance(facts.get("position_obj"), dict)
                     and facts["position_obj"].get("has_position") else None),
        "sources": [{"n": i, "title": esc(c.get("title") or _domain(c.get("url")) or c.get("url")),
                     "url": html.escape(str(c.get("url") or ""), quote=True), "topic": esc(c.get("topic"))}
                    for i, c in enumerate(_dedupe_sources([c for c in source_links or []
                                          if str(c.get("url") or "").lower().startswith(("https://", "http://"))]), 1)],
        "audit": {"checks_passed": not fails, "fails": [esc(f) for f in fails], "adversary": adversary,
                  "withheld": [esc(w) for w in (res.get("withheld") or [])],
                  "transmission_withheld": [esc(w) for w in (res.get("transmission_withheld") or [])],
                  "traces": res.get("traces") or []},
    }


def run_contract(ticker: str, date_str: str, dw: Dict[str, Any], *, reports_dir: Path, **kw: Any) -> Dict[str, Any]:
    """Fail closed on any crash too: the published report never falls back to an LLM-authored document."""
    try:
        return _run_contract(ticker, date_str, dw, reports_dir=reports_dir, **kw)
    except Exception as e:
        logger.exception(f"[{ticker}] research contract crashed")
        from src.logic.order_ticket import void_ticket
        from src.logic.scenario_plans import cancel_unfilled

        safe = ticker.replace(":", "_")
        for fn in (lambda: cancel_unfilled(ticker, date_str), lambda: void_ticket(ticker, date_str, "research contract crashed")):
            try:
                fn()
            except Exception:
                logger.exception(f"[{ticker}] fail-closed cleanup step failed")
        _write([reports_dir / f"{safe}_arbitration.md", reports_dir / f"{safe}_summary.md"],
               f"# {ticker} | RESEARCH CONTRACT ({date_str}) · FAIL (document closed)\n\n"
               f"> **Verdict**: **CASH_SKIP**\n\n- contract crashed: {type(e).__name__}: {e}\n")
        return {"ticker": ticker, "date": date_str, "verdict": "CASH_SKIP", "contract_failures": [f"crash: {e}"]}


def _run_contract(ticker: str, date_str: str, dw: Dict[str, Any], *, side: Optional[str], setup_lane: Optional[str],
                  kind: str, captured_at: datetime, inputs_md: str, sources: List[Tuple[str, str]], position_block: str,
                  tdir: Path, raw_dir: Path, reports_dir: Path, csv_path: Optional[Path] = None,
                  lane_prior_win: Optional[float] = None, lane_prior_ev: Optional[float] = None,
                  report_a: str = "", report_b: str = "",
                  source_links: Optional[List[Dict[str, Any]]] = None,
                  market_tape: Optional[Dict[str, Any]] = None,
                  position: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write the document for one side, assert it, adjudicate PASS/FAIL, persist. Returns watch_data."""
    from src import config
    from src.logic.deep_research.arbitration import _record_research_completed
    from src.logic.order_ticket import EARNINGS_BLOCK_DAYS, void_ticket
    from src.logic.pine_levels import pine_long_plan
    from src.logic.scenario_plans import _rr_floor, cancel_unfilled
    from src.logic.trade_state import trade_state
    from src.tracking.suggestions_ledger import append_suggestion, log_rejected_plan

    safe = ticker.replace(":", "_")
    today = date.fromisoformat(date_str[:10])
    bars = load_bars(csv_path)
    atr = _f(dw.get("RSI2 ATR14")) or _f(dw.get("atr14")) or atr_from_bars(bars)
    em_pct = _f(dw.get("Exp Move Pct 21b")) or 0.0
    forming = bar_is_forming(dw.get("bar_date") or dw.get("time"), captured_at,
                             (config.MARKET_CLOSE_HOUR, config.MARKET_CLOSE_MINUTE))
    try:
        from src.logic.level_validation import earnings_days_for

        e_days = earnings_days_for(ticker, date_str, dw)
    except Exception:
        e_days = None
    try:
        from src.clients.earnings_client import resolve_next_earnings_date

        e_date = str(resolve_next_earnings_date(ticker)[0] or "") or None
    except Exception:
        e_date = None
    from src.logic.level_validation import earnings_reported_on

    reported = earnings_reported_on(dw, date_str) if e_days in (0, None) and (e_date or date_str)[:10] == date_str[:10] else None
    if not reported and e_days is not None and e_days <= 10:
        recent = [str(ev["date"])[:10] for ev in dw.get("_earnings_reaction_events") or []
                  if isinstance(ev, dict) and ev.get("eps_reported") is not None
                  and 0 <= (today - date.fromisoformat(str(ev["date"])[:10])).days <= 7]
        reported = recent[-1] if recent else None
    if reported:
        e_days, e_date = None, None
    from src.logic.actionable_gate import gate_inputs_from_datawindow, is_actionable

    gate_in = gate_inputs_from_datawindow(dw)
    pine = pine_long_plan(dw)
    ts = (trade_state(pine["close"], pine["zone_low"], pine["zone_high"], pine["stop"], pine["target"], atr,
                      fade=gate_in.get("fade_long")) if pine else {"state": "NO_TRADE", "label": "NO TRADE · no plan", "detail": ""})
    from src.logic.pine_levels import rsi2_order_live

    rsi2_ready = setup_lane == "RSI2" and rsi2_order_live(dw)
    manage = str(kind or "").upper() == "MANAGE"
    has_entry_plan = bool(pine) or bool(rsi2_ready)
    block = RSI2_HOLD_DAYS if rsi2_ready else EARNINGS_BLOCK_DAYS
    reg = registry(dw, atr, bars)
    try:
        from src.logic.deep_research import quant_facts as _quant_facts

        quant = _quant_facts.build_quant_facts(ticker, dw, bars, raw_dir=raw_dir, reg=reg, atr=atr, date_str=date_str)
        quant_text = _quant_facts.format_quant_facts(quant)
    except Exception as e:
        logger.warning(f"[{ticker}] quant facts skipped: {e}")
        quant, quant_text = {}, ""
    facts = _facts(dw, forming, atr, " — ".join(x for x in (ts.get("label"), ts.get("detail")) if x),
                   e_days, e_date, position_block)
    facts["earnings_reported"] = reported
    facts["live"] = live_last(sources)
    facts["quant"] = quant
    facts["fade"] = gate_in.get("fade_long")
    facts["source_links"] = source_links or []
    facts["market_tape"] = market_tape or {}
    facts["position_obj"] = position if isinstance(position, dict) and position.get("has_position") else None
    dw_dump = "\n".join(f"{k}: {v}" for k, v in dw.items()
                         if not str(k).startswith("_") and v not in (None, "") and len(str(v)) < 400)
    sections = [("Code facts and level ladder", code_facts(dw, reg, atr, forming, e_date, e_days, ts, bars)),
                ("Data Window export (every field)", dw_dump),
                *(([("Quant facts (code)", quant_text)] if quant_text else [])),
                *[(t, b) for t, b in sources if str(b or "").strip()]]
    sources_md = "\n\n".join(f"[S{i}] {t}\n{b.strip()}" for i, (t, b) in enumerate(sections))
    watch: Dict[str, Any] = {"ticker": ticker, "date": date_str, "side": side, "engine": "contract",
                             "setup_lane": setup_lane, "kind": kind, "bar_forming": forming, "atr14": atr,
                             "next_earnings_date": e_date, "lane_prior_win": lane_prior_win, "lane_prior_ev": lane_prior_ev}
    obj, res, fails, adversary = None, {"levels": {}}, [], "FAIL"
    if side is None:
        fails = ["no document: code pre-filter found no long plan (Pine stop < close < target fails, no RSI2 order)"]
    else:
        from src.logic.deep_research.transmission_rules import rules_prompt_block

        sys_p = (WRITER_SYS.format(ticker=ticker, side=side, floor=_rr_floor(), block=block, stance_days=STANCE_WINDOW_DAYS)
                 + rules_prompt_block())
        pos = facts.get("position_obj") or {}
        legs = "; ".join(f"{_f(o.get('quantity')):+g} {str(o.get('symbol') or '').strip()}"
                         for o in pos.get("options") or [] if _f(o.get("quantity")))
        pos_line = (f"POSITION HELD: {_f(pos.get('quantity')) or 0:g} {pos.get('side', 'LONG')} @ ${(_f(pos.get('average_price')) or 0):,.2f} basis"
                    + (f"; option legs: {legs}" if legs else "")
                    + "\n") if pos.get("has_position") else ""
        user_p = (f"STATE: {ts['state']} ({ts['label']})\nBAR: {'FORMING (not closed)' if forming else 'CLOSED'}\n"
                  f"RSI2 ORDER PENDING: {'yes' if rsi2_ready else 'no'}\n"
                  + (f"HAS_ENTRY_PLAN: {'yes' if has_entry_plan else 'no'}\n" if manage else "")
                  + pos_line
                  + (f"EARNINGS: reported {reported}, already printed; the event has passed, next date unknown\n"
                     if reported else
                     f"EARNINGS: {e_date or 'unknown'} ({'unknown' if e_days is None else str(e_days) + ' trading days'})\n")
                  + f"POSITION MODE: {kind}\n\nSOURCES (citable)\n{sources_md}\n\n"
                  f"RESEARCH INPUTS (LLM-authored, not citable; never copy their numbers)\n{inputs_md}")
        raw = _writer(sys_p, user_p, ticker, sections)
        obj = parse_json(raw)
        if not obj:
            _write([raw_dir / safe / f"{safe}_writer_raw.txt"], raw or "(empty)")
            logger.warning(f"[{ticker}] writer gave no parseable JSON ({len(raw or '')} chars); retrying JSON-only")
            obj = parse_json(_writer(sys_p, user_p + "\n\nYOUR PREVIOUS ANSWER HAD NO PARSEABLE JSON OBJECT. Output ONLY the "
                                     "JSON object, no prose before or after it.", ticker, sections, max_rounds=0))
        def _check(o):
            return check(o, side=side, reg=reg, atr=atr, em_pct=em_pct, forming=forming, state=ts["state"],
                         rsi2_ready=rsi2_ready, earnings_days=e_days, earnings_date=e_date, block_days=block, today=today,
                         sections=sections, dw=dw, live=live_last(sources), quant=quant, tape=market_tape,
                         manage=manage, has_entry_plan=has_entry_plan)

        first = copy.deepcopy(obj)
        res, fails = _check(obj)
        if first and (res.get("withheld") or fails):
            note = "\n".join(f"- {x}" for x in [*fails, *(res.get("withheld") or [])])
            obj2 = parse_json(_writer(sys_p, user_p + f"\n\nYOUR PREVIOUS ANSWER FAILED THESE CHECKS:\n{note}\n"
                                      "Output the full JSON again. Fix each by citing the section that really contains the number "
                                      "(check the [S<n>] index), or drop the number.", ticker, sections, max_rounds=1))
            res2, fails2 = _check(obj2) if obj2 else (res, fails)
            if obj2 and (len(fails2) * 10 + len(res2.get("withheld") or [])) < (len(fails) * 10 + len(res.get("withheld") or [])):
                obj, res, fails = obj2, res2, fails2
    lv = res.get("levels") or {}
    price = lambda k: (lv.get(k) or {}).get("price") or 0.0
    sp = {"side": SIDE_LONG, "entry_type": {"ZONE": "LIMIT", "LIMIT": "LIMIT", "BREAKOUT": "BREAKOUT", "NEXT_OPEN": "NEXT_OPEN"}.get(res.get("entry_kind"), "NO_ENTRY"),
          "entry_zone_low": price("entry_low") or (round(price("stop") + 0.01, 2) if res.get("entry_kind") == "NEXT_OPEN" else 0.0),
          "entry_zone_high": price("entry_high"), "breakout_level": price("trigger"),
          "breakout_stop": price("stop") if price("trigger") else 0.0, "tactical_stop": price("stop"),
          "target_1": price("target_1"), "target_2": price("target_2")}
    if res.get("entry_kind") == "NEXT_OPEN":
        sp.update(entry_max=price("entry_high"), exit_rule="sell next open after a close above EMA5, or after 10 bars")
    if manage:
        watch["status"] = "IN_TRADE"
    prov = {k: v["level"] for k, v in lv.items() if v}
    src = set(prov.get(k) for k in ("entry_low", "entry_high", "stop", "target_1") if prov.get(k))
    watch.update(levels_provenance={k: {"level": v["level"], "source": v["source"], "price": v["price"]} for k, v in lv.items() if v},
                 levels_source="rsi2" if res.get("entry_kind") == "NEXT_OPEN" else ("pine" if src and all(s.startswith("PINE_") for s in src) else "contract"),
                 shares_plan=sp, invalidation={"condition": "DAILY_CLOSE_BELOW", "price_level": sp["tactical_stop"], "rationale": f"stop level {prov.get('stop', 'none')}"})
    if obj:
        watch.update(judge_verdict=res.get("verdict"), veto=res.get("veto"), veto_fact=str(obj.get("veto_fact") or ""),
                     earnings_stance=res.get("stance"), earnings_rationale=str(obj.get("earnings_rationale") or ""),
                     thesis=obj.get("thesis") or [], case_against=obj.get("case_against") or [])

    if not fails and res.get("verdict") == "ENTER" and res.get("entry_kind") != "NEXT_OPEN":
        ok, why = is_actionable(gate_in, {**sp, "ticker": ticker, "side": SIDE_LONG, "kind": kind, "setup_lane": setup_lane,
                                          "earnings_stance": res.get("stance"), "source": watch["levels_source"], "spot": facts["close"]})
        fails += [f"actionable gate: {r}" for r in why] if not ok else []
    if not fails and res.get("entry_kind") != "NONE":
        from src.logic.level_validation import validate_levels

        plan = {**copy.deepcopy(sp), "ticker": ticker, "date": date_str, "side": SIDE_LONG, "kind": kind,
                "setup_lane": setup_lane, "earnings_stance": res.get("stance"), "source": watch["levels_source"],
                "is_judge": watch["levels_source"] != "rsi2", "spot": facts["close"]}
        before = {k: plan.get(k) for k in sp}
        ok, why = validate_levels(plan, dw, SIDE_LONG, ticker=ticker, date_str=date_str)
        fails += [f"level gate: {r}" for r in why] if not ok else []
        changed = [k for k in sp if plan.get(k) != before.get(k)]
        if changed:
            fails.append(f"level gate would repair {', '.join(changed)}")
    if side is not None and not fails:
        plan_md = render(ticker, date_str, side, {**watch, "verdict": res.get("verdict")}, res, [], "pending", facts, obj,
                         quant=quant, source_links=source_links, include_transmission=False)
        sources_md = "\n\n".join(f"[S{i}] {t}\n{b.strip()}" for i, (t, b) in enumerate(sections))
        adv_user = f"SOURCES\n{sources_md}\n\nPLAN\n{plan_md}"
        adv_text = _llm(ADVERSARY_SYS.format(ticker=ticker), adv_user, "ADVERSARY_MODEL", False, 8000)
        if not adversary_has_verdict(adv_text):
            logger.warning(f"[{ticker}] adversary gave no verdict ({len(adv_text)} chars); retrying once")
            adv_text = _llm(ADVERSARY_SYS.format(ticker=ticker), adv_user + "\n\nYour reply must be exactly PASS, or FAIL: plus one sentence. No other text.",
                            "ADVERSARY_MODEL", False, 8000)
        adversary = adversary_verdict(adv_text)
        if adversary != "PASS":
            fails.append("adversarial pass: FAIL")
            watch["adversary_reason"] = adversary_reason(adv_text)
    watch.update(contract_failures=fails, adversary=adversary)

    extra = ""
    if fails:
        watch.update(verdict="HOLD" if manage else "CASH_SKIP", level_gate_rejected=True, gate_reasons=fails, actionable=0, scenarios=[])
        cancel_unfilled(ticker, date_str)
        void_ticket(ticker, date_str, "research contract failed")
        log_rejected_plan(ticker, date_str, watch, fails)
        _write([reports_dir / f"{safe}_watch_levels.json", raw_dir / safe / f"{safe}_watch_levels.json"], json.dumps(watch, indent=2))
    else:
        from src.logic.scenario_plans import attach_scenarios, scenario_markdown
        from src.tracking.trade_rules import compute_position_shares, entry_max_for_rr

        watch["verdict"] = {"ENTER": "ENTER (Next Open)" if res["entry_kind"] == "NEXT_OPEN" else "ENTER (Limit @ Floor)",
                            "ARMED": "STALK"}.get(res["verdict"], res["verdict"])
        watch["scenarios"] = res["scenarios"]
        spot = float(facts["close"] or 0)
        manage_v = res.get("verdict") in MANAGE_VERDICTS
        if sp["tactical_stop"] and sp["target_1"] and not manage_v:
            fill = sp["entry_zone_high"] or sp["breakout_level"] or spot
            sp["recommended_shares"] = compute_position_shares(fill, sp["tactical_stop"])
            sp.setdefault("entry_max", round(entry_max_for_rr(sp["target_1"], sp["tactical_stop"], _rr_floor()), 2))
        if watch["scenarios"]:
            kept = attach_scenarios(watch, ticker, spot, atr or 0, em_pct, e_date, today=today, allow_unskip=False,
                                    pine_stop=(reg.get("PINE_STOP") or {}).get("price") or 0.0)
        else:
            kept = []
            cancel_unfilled(ticker, date_str)
        if kept:
            extra += scenario_markdown(kept, spot)
        try:
            from src.logic.options_menu import attach_options_menu

            attach_options_menu(watch, ticker, spot, em_pct, e_date, kind=kind, position=position)
        except Exception as e:
            logger.warning(f"[{ticker}] options menu skipped: {e}")
        from src.logic.layered_entry import build_layers, layers_markdown

        if res["entry_kind"] in ("ZONE", "LIMIT") and watch["verdict"] != "CASH_SKIP":
            from src.logic import earnings_move
            from src.tracking import trade_rules as _tr

            m = re.search(r"Options implied move: ([\d.]+)%", sources_md or "")
            im = float(m.group(1)) if m else None
            gap = earnings_move.gap_pct(ticker, im) or (round(2 * em_pct, 2) if em_pct else None)
            lay = build_layers(spot, sp["entry_zone_low"], sp["entry_zone_high"], sp["tactical_stop"], sp["target_1"], atr,
                               _tr.DEFAULT_RISK_BUDGET_USD, earnings_days=e_days, earnings_block_days=EARNINGS_BLOCK_DAYS,
                               implied_move_pct=im, gap_pct=gap, equity_usd=_tr.ACCOUNT_EQUITY_USD,
                               event_risk=watch.get("veto") == "EVENT_RISK")
            if lay:
                try:
                    from src.logic.layered_entry import cap_layers_to_position
                    from src.tracking.position_targets import get_target, held_shares

                    lay = cap_layers_to_position(lay, held_shares(ticker), get_target(ticker))
                except Exception as e:
                    logger.debug(f"[{ticker}] position-target sizing skipped: {e}")
                sp["layers"] = lay
        watch["actionable"] = 1 if watch["verdict"].startswith("ENTER") else 0
        if sp.get("layers"):
            sp["layers"]["status"] = "ACTIVE" if watch["actionable"] else "WATCH (not ENTER)"
            extra = (extra + "\n\n" if extra else "") + layers_markdown(sp["layers"], ticker)
        _write([tdir / f"{safe}_watch_levels.json", raw_dir / safe / f"{safe}_watch_levels.json"], json.dumps(watch, indent=2))
        from src.tracking.watch_manager import upsert_watch_target

        watch["suggestion_id"] = append_suggestion({
            "ticker": ticker, "date": date_str, "source": "contract", "side": SIDE_LONG, "entry_type": sp["entry_type"],
            "entry_low": sp["entry_zone_low"], "entry_high": sp["entry_zone_high"], "breakout_level": sp["breakout_level"],
            "stop": sp["tactical_stop"], "target_1": sp["target_1"], "target_2": sp["target_2"],
            "planned_rr": res.get("rr_at_entry"), "verdict": watch["verdict"], "judge_verdict": res.get("verdict"),
            "shares_plan": sp, "gate_status": "PASS", "setup_lane": setup_lane, "kind": kind, "atr_at_signal": atr,
            "spot_at_signal": spot or None, "rr_at_market_at_signal": res.get("rr_at_market"),
            "lane_prior_win": lane_prior_win, "lane_prior_ev": lane_prior_ev, "_datawindow": dw,
            "notes": f"contract: {watch['verdict']} ({watch['levels_source']})",
        })
        try:
            from src.logic.order_ticket import MEASURED_SOURCES, build_ticket, persist_and_notify

            tk = build_ticket(watch, spot, date_str, skip_superseded=watch["levels_source"] in MEASURED_SOURCES)
            if tk:
                watch["ticket"] = tk
                persist_and_notify(tk)
            else:
                void_ticket(ticker, date_str, "contract plan is not ENTER")
        except Exception as e:
            logger.warning(f"[{ticker}] ticket build skipped: {e}")
        upsert_watch_target(watch)
        _write([reports_dir / f"{safe}_watch_levels.json"], json.dumps(watch, indent=2))
    report = render(ticker, date_str, side, watch, res, fails, adversary, facts, obj, extra, display=(reg, sections),
                    quant=quant, source_links=source_links)
    _write([reports_dir / f"{safe}_arbitration.md"], report)
    try:
        rep = build_report_obj(ticker, date_str, side, watch, res, fails, adversary, facts, obj, quant, source_links,
                               reg=reg)
        _write([reports_dir / f"{safe}_report.json"], json.dumps(rep, indent=2))
    except Exception as e:
        logger.warning(f"[{ticker}] report json skipped: {e}")
    chains = [(p.name, p.read_text(encoding="utf-8", errors="ignore"))
              for d in {Path(tdir), Path(raw_dir), Path(raw_dir) / safe} for p in sorted(d.glob("*options_chain*.json"))]
    allowed = _allowed_numbers(reg, dw, list(sections) + chains)
    for name, title, body in (("summary", "Model A thesis", report_a), ("independent", "Model B independent thesis", report_b)):
        prior = reports_dir / f"{safe}_{name}.md"
        old = prior.read_text(encoding="utf-8") if prior.exists() else ""
        base = old if old and "RESEARCH CONTRACT" not in old[:200] and "not published" not in old[:300] else body
        if not base.strip():
            continue
        marked, n = annotate(base, allowed)
        cut = report.find("## 🔒 RESEARCH CONTRACT")
        contract = report[cut:] if cut >= 0 else report
        banner = (f"> **{title} (LLM narrative).** Code-verified decision and levels follow. "
                  f"{n} dollar figure(s) in this text match no source, data column or level and are marked ⚠️; "
                  f"where the narrative disagrees with the verified levels, the levels govern.")
        _write([reports_dir / f"{safe}_{name}.md"], f"{marked.rstrip()}\n\n---\n\n{banner}\n\n{contract}\n")
    _record_research_completed(ticker, date_str, dw, sp)
    logger.info(f"[{ticker}] contract: {watch['verdict']} · asserts {'FAIL' if fails else 'PASS'} · adversary {adversary}")
    return watch
