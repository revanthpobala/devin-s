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
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SIDE_LONG = "LONG"
VERDICTS = ("ENTER", "ARMED", "WATCH", "WATCH (EVENT RISK)", "CASH_SKIP")
VETOES = ("NONE", "EVENT_RISK", "HARD_FUNDAMENTAL")
STANCES = ("NONE", "EXIT_BEFORE", "HOLD_THROUGH", "EARNINGS_PLAY")
ENTRY_KINDS = ("ZONE", "LIMIT", "BREAKOUT", "NEXT_OPEN", "NONE")
SCENARIO_KINDS = ("PULLBACK", "BREAKOUT")
STANCE_WINDOW_DAYS = 21
RSI2_HOLD_DAYS = 10
PRICE_TOL = 0.005

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


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if x > 0 else None
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


def choose_side(dw: Dict[str, Any], setup_lane: Optional[str]) -> Optional[str]:
    """Document side, set before writing. Shorts carry no measured edge, so the only side is LONG, and only with a plan."""
    from src.logic.pine_levels import pine_long_plan, rsi2_order_live

    if pine_long_plan(dw):
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
 "verdict": "ENTER|ARMED|WATCH|WATCH (EVENT RISK)|CASH_SKIP",
 "veto": "NONE|EVENT_RISK|HARD_FUNDAMENTAL",
 "veto_fact": "dated fact behind a veto, else empty",
 "entry": {{"kind": "ZONE|LIMIT|BREAKOUT|NEXT_OPEN|NONE", "low": "<LEVEL or null>", "high": "<LEVEL or null>", "trigger": "<LEVEL or null>"}},
 "stop": "<LEVEL>", "target_1": "<LEVEL>", "target_2": "<LEVEL or null>",
 "scenarios": [{{"kind": "PULLBACK|BREAKOUT", "trigger": "<LEVEL>", "zone_low": "<LEVEL or null>", "stop": "<LEVEL>",
                "target": "<LEVEL>", "window_end": "YYYY-MM-DD", "confirm": ["check"], "thesis": "one line"}}],
 "earnings_stance": "NONE|EXIT_BEFORE|HOLD_THROUGH|EARNINGS_PLAY",
 "earnings_rationale": "one line",
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
   floor: use entry kind NONE with a PULLBACK/BREAKOUT scenario, or CASH_SKIP.
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
   R:R at the trigger at least the floor. When unsure, CASH_SKIP with an empty plan is always valid."""

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
            msg = client.chat.completions.create(**kw).choices[0].message
        except Exception as e:
            logger.warning(f"[{ticker}] writer call failed: {e}")
            return ""
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
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


def _prose_fields(obj: Dict[str, Any]) -> List[Tuple[str, str]]:
    out = [("veto_fact", obj.get("veto_fact")), ("earnings_rationale", obj.get("earnings_rationale"))]
    for key in LIST_KEYS:
        out += [(f"{key}[{i}]", t) for i, t in enumerate(obj.get(key) or [])]
    for i, s in enumerate(obj.get("scenarios") or []):
        if isinstance(s, dict):
            out.append((f"scenarios[{i}].thesis", s.get("thesis")))
            out += [(f"scenarios[{i}].confirm[{j}]", c) for j, c in enumerate(s.get("confirm") or [])]
    return [(k, str(v)) for k, v in out if v]


def check(obj: Optional[Dict[str, Any]], *, side: str, reg: Dict[str, Dict[str, Any]], atr: Optional[float],
          em_pct: float, forming: bool, state: str, rsi2_ready: bool, earnings_days: Optional[int],
          earnings_date: Optional[str], block_days: int, today: date, sections: List[Tuple[str, str]],
          dw: Dict[str, Any], live: Optional[float] = None) -> Tuple[Dict[str, Any], List[str]]:
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
    if verdict not in VERDICTS:
        fails.append(f"verdict {obj.get('verdict')!r} not in {VERDICTS}")
    if veto not in VETOES:
        fails.append(f"veto {obj.get('veto')!r} not in {VETOES}")
    if stance not in STANCES:
        fails.append(f"earnings_stance {obj.get('earnings_stance')!r} not in {STANCES}")
    traces: List[Dict[str, Any]] = []
    withheld: List[str] = []
    bad: set = set()
    for k, v in _prose_fields(obj):
        rows, why = trace(k, v, sections, dw, reg)
        if why and (k == "earnings_rationale" or k.startswith(tuple(f"{x}[" for x in LIST_KEYS))):
            withheld += why
            bad.add(k)
            continue
        traces += rows
        fails += why
    if "earnings_rationale" in bad:
        obj["earnings_rationale"] = ""
    for key in LIST_KEYS:
        if isinstance(obj.get(key), list):
            obj[key] = [t for i, t in enumerate(obj[key]) if f"{key}[{i}]" not in bad]
    if veto != "NONE" and not str(obj.get("veto_fact") or "").strip():
        fails.append(f"veto {veto} without veto_fact")
    if veto == "HARD_FUNDAMENTAL" and (verdict != "CASH_SKIP" or obj.get("scenarios")):
        fails.append("HARD_FUNDAMENTAL requires CASH_SKIP and no scenarios")
    if verdict == "WATCH (EVENT RISK)" and veto != "EVENT_RISK":
        fails.append("WATCH (EVENT RISK) requires veto EVENT_RISK")
    if earnings_days is not None and 0 <= earnings_days <= block_days and verdict not in ("WATCH (EVENT RISK)", "CASH_SKIP"):
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
    L["stop"] = _level(reg, obj.get("stop"), "stop", fails, has_plan)
    L["target_1"] = _level(reg, obj.get("target_1"), "target_1", fails, has_plan)
    L["target_2"] = _level(reg, obj.get("target_2"), "target_2", fails, False)
    p = {k: (v or {}).get("price") for k, v in L.items()}

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
        if not pine_t and em_pct > 0 and close and p["target_1"] > close * (1 + T1_MAX_EXPECTED_MOVE_MULT * em_pct / 100) + PRICE_TOL:
            fails.append(f"target_1 {p['target_1']} beyond {T1_MAX_EXPECTED_MOVE_MULT:g}x the 21-bar expected move")

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
    return res, main_fails


LEVEL_LABELS = {
    "PINE_ZONE_LOW": "Pine zone low", "PINE_ZONE_HIGH": "Pine zone high", "PINE_STOP": "Pine stop", "PINE_TARGET": "Pine target",
    "ATR_FLOOR_STOP": "ATR-floor stop", "RR_FLOOR_LIMIT": "R:R-floor entry limit", "VP_VAL": "value-area low",
    "VP_VAH": "value-area high", "VP_POC": "volume point of control", "DARVAS_BOX_TOP": "Darvas box top",
    "OPT_GAMMA_FLIP": "gamma flip", "CLOSE": "close",
}
_CITE_GROUP = re.compile(r"(?:\s*\[(?:S\d+|DW:[^\]]+|L:[A-Z0-9_]+)\])+")
_NAME_TAG = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\s*\[L:\1\]")
_BARE_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def level_label(name: str) -> str:
    if name in LEVEL_LABELS:
        return LEVEL_LABELS[name]
    m = re.fullmatch(r"KEY_(SUPPORT|RESISTANCE)_(\d+)", name)
    return f"key {m.group(1).lower()} {m.group(2)}" if m else name.lower().replace("_", " ")


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
                n = re.split(r"\s*\(", sections[int(tok[1:])][0])[0].strip()
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
                 "WATCH": "WATCH: no order yet", "WATCH (EVENT RISK)": "WATCH: event risk inside the hold", "CASH_SKIP": "NO TRADE"}


def _rr(target: Optional[float], entry: Optional[float], stop: Optional[float]) -> Optional[float]:
    return (target - entry) / (entry - stop) if target and entry and stop and stop < entry < target else None


def _decision(watch: Dict[str, Any], res: Dict[str, Any], facts: Dict[str, Any], fails: List[str], h) -> List[str]:
    from src.logic.scenario_plans import _rr_floor

    lv = res.get("levels") or {}
    p = lambda k: (lv.get(k) or {}).get("price")
    v = str(watch.get("verdict") or "CASH_SKIP")
    lo, hi, trig, stop, t1, t2 = p("entry_low"), p("entry_high"), p("trigger"), p("stop"), p("target_1"), p("target_2")
    close, live, atr, kind = _f(facts.get("close")), _f(facts.get("live")), _f(facts.get("atr")), res.get("entry_kind")
    why: List[str] = []
    if watch.get("veto") not in (None, "NONE"):
        why.append(f"{str(watch['veto']).replace('_', ' ').capitalize()}: {h(watch.get('veto_fact'))}")
    if live and t1 and live >= t1:
        why.append(f"Live ${live:.2f} is already past target 1 (${t1:.2f}). The move this plan waits for has happened; "
                   "re-plan from a new base, do not chase.")
    elif live and stop and live <= stop:
        why.append(f"Live ${live:.2f} is at or below the stop (${stop:.2f}). The plan is broken.")
    for f in fails:
        if f.startswith("adversarial pass"):
            r = watch.get("adversary_reason")
            why.append("An independent reviewer rejected the plan" + (f": {h(r)}" if r else "."))
        elif not f.startswith("live quote"):
            why.append(f"Check failed: {h(f)}")
    out = [f"## 🧭 DECISION: {VERDICT_WORDS.get(v, v)}", ""]
    if why:
        out += ["**Why:**", *[f"- {w}" for w in why], ""]
    px = f"close ${_fmt(close)} on {facts.get('bar_date')}" + (" (bar still forming)" if facts.get("forming") else "")
    out.append(f"- **Price:** {px}" + (f" · live ${live:.2f}" if live else ""))
    top = hi or trig
    if stop and t1:
        if fails:
            out.append("- _The levels below were rejected; they are not an order._")
        if kind in ("ZONE", "LIMIT") and lo and hi:
            out.append(f"- **Buy zone:** ${lo:.2f} – ${hi:.2f} (limit order; do not chase)")
        elif kind == "BREAKOUT" and trig:
            out.append(f"- **Buy:** on a break above ${trig:.2f}")
        elif kind == "NEXT_OPEN" and hi:
            out.append(f"- **Buy:** at the next open, no higher than ${hi:.2f}")
        out.append(f"- **Stop:** ${stop:.2f}" + (f" ({(top - stop) / atr:.1f} ATR under the entry)" if top and atr else ""))
        out.append(f"- **Targets:** ${t1:.2f}" + (f" then ${t2:.2f}" if t2 else ""))
        rr = [(f"{x:.2f} {where}") for x, where in ((_rr(t1, top, stop), "filled at the entry"), (_rr(t1, close, stop), "at the close"),
                                                    (_rr(t1, live, stop), "at the live price")) if x is not None]
        if rr:
            out.append(f"- **Reward:risk to target 1:** {' · '.join(rr)}")
        rr_live = _rr(t1, live or close, stop)
        if kind in ("ZONE", "LIMIT") and rr_live is not None and rr_live < _rr_floor():
            out.append(f"- **Buying now is a chase** (reward:risk {rr_live:.2f}). The edge exists only if price pulls back to the buy zone.")
        out.append(f"- **Invalidation:** a daily close below ${stop:.2f}")
    stance = res.get("stance")
    if stance:
        out.append(f"- **Earnings:** {str(stance).replace('_', ' ').lower()}" + (f" – {h((watch.get('_obj') or {}).get('earnings_rationale') or '')}".rstrip(" –")))
    return out + [""]


def render(ticker: str, date_str: str, side: Optional[str], watch: Dict[str, Any], res: Dict[str, Any],
           fails: List[str], adversary: str, facts: Dict[str, Any], obj: Optional[Dict[str, Any]],
           extra_md: str = "", display: Optional[Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str]]]] = None) -> str:
    def h(t: Any) -> str:
        return humanize(str(t), *display) if display else str(t)

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
                          *(["", "## Position", str(facts["position"]).strip()] if facts.get("position") else []), *(["", extra_md] if extra_md else [])])
    o = obj or {}
    lv = res.get("levels") or {}
    lines = [f"# {ticker} | ⚖️ SENIOR PM ARBITRATION ({date_str}) · {side.lower()} side", "",
             *_decision({**watch, "_obj": o}, res, facts, fails, h)]
    if not fails and any(lv.values()):
        lines += _directives(watch, res, facts, lv)
    for key, title in (("fundamentals", "## 📊 FUNDAMENTALS"), ("news_synthesis", "## 📰 NEWS"),
                       ("thesis", "## 🟢 THE CASE FOR"), ("case_against", "## 🔴 THE CASE AGAINST")):
        items = [h(t) for t in (o.get(key) or [])]
        if items:
            lines += [title, *[f"- {t}" for t in items], ""]
    ruling = [(t, [h(x) for x in v]) for t, v in (("Where both reports agree", o.get("concurrence")),
                                                    ("Where they disagree", o.get("conflicts"))) if v]
    lines += ["## ⚖️ THE JUDGE'S FINAL RULING"]
    for title, items in ruling:
        lines += [f"* **{title}:**", *[f"    * {t}" for t in items]]
    lines += [f"* **Final Verdict:** **{watch.get('verdict')}**"
              + (f" · veto {watch.get('veto')}: {h(watch.get('veto_fact'))}" if watch.get("veto") not in (None, "NONE") else ""), ""]
    if extra_md:
        lines += [extra_md, ""]
    lines += ["---", "", "## 🔒 Audit trail (how every number was checked)", "",
              f"Code checks {'passed' if not fails else 'failed'} · independent review {adversary.lower()}", "", bar, stats, f"- {earn}"]
    if any(lv.values()):
        lines += ["", "| plan field | level | price | source |", "|---|---|---|---|"]
        lines += [f"| {k.replace('_', ' ')} | {level_label(v['level'])} | {v['price']:.2f} | {v['source']} |" for k, v in lv.items() if v]
    if facts.get("position"):
        lines += ["", "### Position", str(facts["position"]).split("• MANDATORY")[0].strip()]
    if res.get("traces"):
        lines += ["", "### Where each number came from", "", "| field | number | source |", "|---|---|---|"]
        lines += [f"| {t['field']} | {t['number']} | {h(t['cite']).strip(' ()') or t['source']} |" for t in res["traces"]]
    if res.get("withheld"):
        lines += ["", "### Lines removed (a number in them appears in no source)", *[f"- {h(w)}" for w in res["withheld"]]]
    if fails:
        lines += ["", "### Failed checks (raw)", *[f"- {f}" for f in fails]]
    return "\n".join(lines)


def _directives(watch: Dict[str, Any], res: Dict[str, Any], facts: Dict[str, Any], lv: Dict[str, Any]) -> List[str]:
    sp = watch.get("shares_plan") or {}
    out = []
    if sp.get("recommended_shares"):
        out.append(f"* **Shares:** {sp['recommended_shares']} at the risk budget")
    menu = watch.get("options_menu") or {}
    tiers = [(t, menu.get(k)) for t, k in (("Tactical spread (30-50 DTE)", "tactical_spread"), ("LEAPS (6-12 months)", "leaps"),
                                           ("Income / CSP (20-50 DTE)", "income_or_csp")) if (menu.get(k) or {}).get("structure") not in (None, "NONE")]
    out += [f"* **{t}:** {m.get('structure')} - {m.get('summary')}" for t, m in tiers]
    return ["## 🎯 FINAL ACTIONABLE DIRECTIVES", *out, ""] if out else []


def _facts(dw: Dict[str, Any], forming: bool, atr: Optional[float], state_label: str,
           earnings_days: Optional[int], earnings_date: Optional[str], position: str) -> Dict[str, Any]:
    return {"bar_date": dw.get("bar_date") or dw.get("time"), "forming": forming, "close": dw.get("close") or dw.get("Close"),
            "atr": atr, "em_pct": _f(dw.get("Exp Move Pct 21b")) or 0.0, "iv_rank": dw.get("Energy IV Rank Pct"),
            "ext_z": dw.get("Ext Z Self Relative"), "action": dw.get("Action Long Code"), "rvol": dw.get("RVOL Vs Avg"),
            "state_label": state_label, "earnings_days": earnings_days, "earnings_date": earnings_date,
            "position": position if position and "no active" not in position.lower() else ""}


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
                  report_a: str = "", report_b: str = "") -> Dict[str, Any]:
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
    block = RSI2_HOLD_DAYS if rsi2_ready else EARNINGS_BLOCK_DAYS
    reg = registry(dw, atr, bars)
    facts = _facts(dw, forming, atr, f"{ts['label']} — {ts['detail']}", e_days, e_date, position_block)
    facts["earnings_reported"] = reported
    facts["live"] = live_last(sources)
    dw_dump = "\n".join(f"{k}: {v}" for k, v in dw.items()
                         if not str(k).startswith("_") and v not in (None, "") and len(str(v)) < 400)
    sections = [("Code facts and level ladder", code_facts(dw, reg, atr, forming, e_date, e_days, ts, bars)),
                ("Data Window export (every field)", dw_dump),
                *[(t, b) for t, b in sources if str(b or "").strip()]]
    sources_md = "\n\n".join(f"[S{i}] {t}\n{b.strip()}" for i, (t, b) in enumerate(sections))
    watch: Dict[str, Any] = {"ticker": ticker, "date": date_str, "side": side, "engine": "contract",
                             "setup_lane": setup_lane, "kind": kind, "bar_forming": forming, "atr14": atr,
                             "next_earnings_date": e_date, "lane_prior_win": lane_prior_win, "lane_prior_ev": lane_prior_ev}
    obj, res, fails, adversary = None, {"levels": {}}, [], "FAIL"
    if side is None:
        fails = ["no document: code pre-filter found no long plan (Pine stop < close < target fails, no RSI2 order)"]
    else:
        sys_p = WRITER_SYS.format(ticker=ticker, side=side, floor=_rr_floor(), block=block, stance_days=STANCE_WINDOW_DAYS)
        user_p = (f"STATE: {ts['state']} ({ts['label']})\nBAR: {'FORMING (not closed)' if forming else 'CLOSED'}\n"
                  f"RSI2 ORDER PENDING: {'yes' if rsi2_ready else 'no'}\n"
                  + (f"EARNINGS: reported {reported}, already printed; the event has passed, next date unknown\n"
                     if reported else
                     f"EARNINGS: {e_date or 'unknown'} ({'unknown' if e_days is None else str(e_days) + ' trading days'})\n")
                  + f"POSITION MODE: {kind}\n\nSOURCES (citable)\n{sources_md}\n\n"
                  f"RESEARCH INPUTS (LLM-authored, not citable; never copy their numbers)\n{inputs_md}")
        obj = parse_json(_writer(sys_p, user_p, ticker, sections))
        def _check(o):
            return check(o, side=side, reg=reg, atr=atr, em_pct=em_pct, forming=forming, state=ts["state"],
                         rsi2_ready=rsi2_ready, earnings_days=e_days, earnings_date=e_date, block_days=block, today=today,
                         sections=sections, dw=dw, live=live_last(sources))

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
        plan_md = render(ticker, date_str, side, {**watch, "verdict": res.get("verdict")}, res, [], "pending", facts, obj)
        sources_md = "\n\n".join(f"[S{i}] {t}\n{b.strip()}" for i, (t, b) in enumerate(sections))
        adv_text = _llm(ADVERSARY_SYS.format(ticker=ticker), f"SOURCES\n{sources_md}\n\nPLAN\n{plan_md}", "ADVERSARY_MODEL", False, 8000)
        adversary = adversary_verdict(adv_text)
        if adversary != "PASS":
            fails.append("adversarial pass: FAIL")
            watch["adversary_reason"] = adversary_reason(adv_text)
    watch.update(contract_failures=fails, adversary=adversary)

    extra = ""
    if fails:
        watch.update(verdict="CASH_SKIP", level_gate_rejected=True, gate_reasons=fails, actionable=0, scenarios=[])
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
        if sp["tactical_stop"] and sp["target_1"]:
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

            attach_options_menu(watch, ticker, spot, em_pct, e_date)
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
    report = render(ticker, date_str, side, watch, res, fails, adversary, facts, obj, extra, display=(reg, sections))
    _write([reports_dir / f"{safe}_arbitration.md"], report)
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
