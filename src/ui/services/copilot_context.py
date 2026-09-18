"""
Quantitative context extraction, SEC filings aggregation, and LLM prompt assembly for Copilot.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config
from src.ui.routes.research import extract_report_card as _extract_report_card
from src.ui.state import (
    ACTIVE_RESEARCH_SUBPROCS,
    ACTIVE_RESEARCH_WORKERS,
    LOGS_DIR,
    append_log as _append_log,
    get_db as _get_db,
    init_db as _init_db,
)

logger = logging.getLogger("ui_server")

_KNOWN_TICKER_SET = None

def _get_known_ticker_set() -> set:
    """Returns the set of legitimate stock symbols across universe sources."""
    global _KNOWN_TICKER_SET
    if _KNOWN_TICKER_SET is not None:
        return _KNOWN_TICKER_SET

    s = set()
    # 0. Primary Universe from All_tickrs/company_tickers.json (10,391 US tickers)
    all_tickrs_json = config.BASE_DIR / "All_tickrs" / "company_tickers.json"
    if all_tickrs_json.exists():
        try:
            sec_dict = json.loads(all_tickrs_json.read_text(encoding="utf-8"))
            for item in sec_dict.values() if isinstance(sec_dict, dict) else sec_dict:
                t = str(item.get("ticker", "")).strip().upper()
                if t and t.isalnum() and len(t) <= 5:
                    s.add(t)
        except Exception:
            pass

    # 1. SPX constituents
    spx_csv = config.BASE_DIR / "EveryDay" / "SPX-constituents.csv"
    if spx_csv.exists():
        try:
            import csv
            with open(spx_csv, "r", encoding="utf-8") as f:
                for row in csv.reader(f):
                    if row and row[0].strip():
                        s.add(row[0].strip().upper())
        except Exception:
            pass

    # 2. Reports
    rep_dir = config.BASE_DIR / "reports"
    if rep_dir.exists():
        for d in os.listdir(rep_dir):
            day_p = rep_dir / d
            if day_p.is_dir():
                for f in os.listdir(day_p):
                    if f.endswith("_summary.md") or f.endswith("_arbitration.md"):
                        s.add(f.split("_")[0].upper())

    # 3. Watch DB
    try:
        with _get_db() as conn:
            rows = conn.cursor().execute("SELECT ticker FROM active_stalking_targets").fetchall()
            for r in rows:
                if r and r[0]:
                    s.add(r[0].upper())
    except Exception:
        pass

    # 4. SEC EDGAR full universe fallback
    cik_json = config.BASE_DIR / "data" / "sec_cik_map.json"
    if cik_json.exists():
        try:
            sec_map = json.loads(cik_json.read_text(encoding="utf-8"))
            for t in sec_map.keys():
                if t and t.isalnum() and len(t) <= 5:
                    s.add(t.upper())
        except Exception:
            pass

    # Core high-volume symbols & ETFs
    s.update({"SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "PLTR", "AVGO", "SMCI", "COIN", "MSTR", "UBER", "DIS", "BA", "BABA", "CRM", "INTC", "QCOM", "TXN", "MU", "PANW", "CRWD", "NOW", "SNOW", "SHOP", "SQ", "PYPL", "ABNB", "ARM", "EIX", "PCG", "SO", "DUK", "NEE", "CEG", "VST", "NRG", "LLY", "NVO", "UNH", "JNJ", "PFE"})
    _KNOWN_TICKER_SET = s
    return _KNOWN_TICKER_SET


_COMPANY_NAME_CACHE: Dict[str, str] = {}
_DYNAMIC_NAME_TO_TICKER: Dict[str, str] = {}


def _init_company_data():
    """Dynamically indexes 10,000+ public companies from SEC EDGAR company_tickers.json without hardcoding."""
    global _COMPANY_NAME_CACHE, _DYNAMIC_NAME_TO_TICKER
    if _COMPANY_NAME_CACHE and _DYNAMIC_NAME_TO_TICKER:
        return
    clean_suffixes = re.compile(
        r'\b(corp|corporation|inc|incorporated|ltd|limited|co|company|holdings|plc|group|sa|nv|lp|llc|class [a-z]|com)\b',
        re.IGNORECASE
    )
    all_tickrs_json = config.BASE_DIR / "All_tickrs" / "company_tickers.json"
    if all_tickrs_json.exists():
        try:
            data = json.loads(all_tickrs_json.read_text(encoding="utf-8"))
            items = data.values() if isinstance(data, dict) else data
            for item in items:
                sym = str(item.get("ticker", "")).strip().upper()
                title = str(item.get("title", "")).strip()
                if sym and title and len(sym) <= 5 and sym.isalnum():
                    _COMPANY_NAME_CACHE[sym] = title
                    _DYNAMIC_NAME_TO_TICKER[title.upper()] = sym
                    raw_clean = clean_suffixes.sub('', title).strip(' ,.-/')
                    cleaned = raw_clean.upper()
                    if len(cleaned) >= 3 and cleaned not in _DYNAMIC_NAME_TO_TICKER:
                        _DYNAMIC_NAME_TO_TICKER[cleaned] = sym
                    unspaced = cleaned.replace(' ', '').replace('-', '')
                    if len(unspaced) >= 3 and unspaced not in _DYNAMIC_NAME_TO_TICKER:
                        _DYNAMIC_NAME_TO_TICKER[unspaced] = sym
                    camel_words = re.findall(r'[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)', raw_clean)
                    if len(camel_words) > 1:
                        spaced = ' '.join(camel_words).upper()
                        if len(spaced) >= 3 and spaced not in _DYNAMIC_NAME_TO_TICKER:
                            _DYNAMIC_NAME_TO_TICKER[spaced] = sym
        except Exception as e:
            logger.debug(f"Error loading SEC company tickers map: {e}")

    # Colloquial trader abbreviations/aliases that differ from formal SEC titles
    colloquial_trader_aliases = {
        "PACIFIC GAS": "PCG",
        "PACIFIC GAS & ELECTRIC": "PCG",
        "PACIFIC GAS AND ELECTRIC": "PCG",
        "PGE": "PCG",
        "EDISON": "EIX",
        "EDISON INTERNATIONAL": "EIX",
        "GOOGLE": "GOOGL",
        "ALPHABET": "GOOGL",
        "FACEBOOK": "META",
        "WALMART": "WMT",
        "WAL-MART": "WMT",
        "UI PATH": "PATH",
        "UIPATH": "PATH",
    }
    for alias_name, alias_sym in colloquial_trader_aliases.items():
        if alias_name not in _DYNAMIC_NAME_TO_TICKER:
            _DYNAMIC_NAME_TO_TICKER[alias_name] = alias_sym


def _get_company_name(ticker: str) -> str:
    """Returns official company name or common title for a ticker symbol."""
    if not ticker or ticker in ("GENERAL", "AUTO", "NONE", ""):
        return "Broad Market"
    _init_company_data()
    return _COMPANY_NAME_CACHE.get(ticker.upper(), ticker.upper())


def _detect_ticker_metadata(question: str, explicit_ticker: str = None, history: list = None) -> dict:
    """
    Extracts all tickers and tracks whether each ticker was explicitly specified ($TICKER / modal / keyword)
    or inferred from casual company names / conversational words.
    """
    _init_company_data()
    known = _get_known_ticker_set()
    detected = []
    matched_by: Dict[str, str] = {}

    q_clean = question

    # 1. Explicit $TICKER in prompt (e.g. $AAPL, $WMT, $EIX, $META, $UI, $PATH)
    dollar_matches = re.findall(r'\$([A-Za-z]{1,5})\b', question)
    for m in dollar_matches:
        mu = m.upper()
        if (mu in known or len(mu) >= 2) and mu not in detected:
            detected.append(mu)
            matched_by[mu] = "dollar"

    # 2. '[TICKER] stock/shares' pattern (e.g. 'check EIX stock as well', 'AAPL shares')
    post_stock_stopwords = {
        "AS", "WELL", "IS", "WAS", "ARE", "HAS", "HAD", "CAN", "WILL", "FOR", "ON", "AND", "OR", "TO",
        "IN", "AT", "BY", "OF", "WITH", "THAT", "THIS", "FROM", "NOW", "LIKE", "SO", "DO", "BE", "GO",
        "IF", "MY", "NO", "UP", "AN", "THE", "A", "PRICE", "PRICES", "CHART", "OPTIONS", "CALL", "PUT",
        "TRADE", "TRADES", "LOOKS", "GOING", "DROP", "FALL", "GAIN", "RISE", "TODAY", "YESTERDAY", "TOMORROW",
        "STEP", "STEPS", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
    }
    for m in re.findall(r'\b([A-Za-z]{1,5})\s+(?:stock|shares|ticker)\b', q_clean, re.IGNORECASE):
        mu = m.upper()
        if mu in known and mu not in post_stock_stopwords and mu not in detected:
            detected.append(mu)
            matched_by[mu] = "keyword"

    # 3. 'stock/ticker [TICKER]' pattern (e.g. 'stock PATH', 'ticker AMD')
    kw_matches = re.findall(r'(?:ticker|stock|symbol|quote for)\s+([A-Za-z]{1,5})\b', q_clean, re.IGNORECASE)
    for m in kw_matches:
        mu = m.upper()
        if mu in known and mu not in post_stock_stopwords and mu not in detected:
            detected.append(mu)
            matched_by[mu] = "keyword"

    # 4. Integrate explicit_ticker if provided from active modal / focus
    # If the user is inside an active modal (e.g. WMT), explicit_ticker is top focus unless user specifically wrote a $TICKER
    if explicit_ticker:
        exp_u = explicit_ticker.strip().upper()
        if exp_u not in ("GENERAL", "AUTO", "NONE", "ALL", ""):
            has_prompt_ticker = any(matched_by.get(t) in ("dollar", "keyword") for t in detected)
            if exp_u in detected:
                detected.remove(exp_u)
            if has_prompt_ticker:
                detected.append(exp_u)
            else:
                detected.insert(0, exp_u)
            matched_by[exp_u] = "modal"

    # 5. Dynamic SEC EDGAR Company Name Matching (O(1) n-gram token matching)
    stopwords_single = {
        "A", "AN", "ON", "IT", "SO", "DO", "BE", "AT", "BY", "IN", "IS", "MY", "NO", "OR", "TO", "UP", "US", "WE",
        "HE", "ME", "OF", "AND", "THE", "BUT", "NOT", "YOU", "ALL", "NOW", "CAN", "SEE", "FOR", "ARE", "HAS", "HAD",
        "WAS", "ONE", "OUT", "DAY", "WHO", "DID", "ITS", "LET", "SAY", "SHE", "TOO", "USE", "NEW", "OLD", "TWO",
        "WAY", "MAN", "TOP", "BIG", "NET", "BEST", "NEXT", "GOOD", "TRUE", "FREE", "MORE", "REAL", "PLAY", "TEST",
        "RULE", "MOVE", "HOLD", "LOOK", "TAKE", "COME", "JUST", "MAKE", "KNOW", "WELL", "ALSO", "LIKE", "SAME",
        "BOTH", "EACH", "INTO", "OVER", "NEAR", "SIDE", "DAYS", "TIME", "DATE", "WEEK", "YEAR", "HIGH", "LOW",
        "OPEN", "LAST", "GAIN", "DROP", "FALL", "RISE", "CALL", "PUT", "LONG", "SHORT", "STOP", "LOSS", "ZONE",
        "RISK", "COST", "DEBT", "CASH", "RATE", "PEER", "VIEW", "SHOW", "TELL", "GIVE", "GET", "WHAT", "WHEN",
        "WHY", "HOW", "FAST", "SLOW", "VERY", "MUCH", "LESS", "THEN", "THAN", "SOME", "SUCH", "EVEN", "MOST",
        "ONLY", "BEEN", "HAVE", "WERE", "WILL", "HELP", "PLAN", "RUNS", "CHAT",
        "STEP", "STEPS", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
        "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
        "CALLS", "PUTS", "LEAP", "LEAPS", "STRIKE", "STRIKES", "TRADE", "TRADES", "PRICE", "PRICES",
        "BUY", "BUYS", "SELL", "SELLS", "HOLDS", "BOUGHT", "SOLD", "RUN", "PLANS", "STOPS", "LOSSES", "ZONES",
        "TARGET", "TARGETS", "ENTRY", "ENTRIES", "ORDER", "ORDERS", "FLOW", "FLOWS", "SWEEP", "SWEEPS",
        "BLOCK", "BLOCKS", "GAINS", "DROPS", "FALLS", "RISES", "RATES", "TERM", "TERMS", "WEEKS", "YEARS",
        "HIGHS", "LOWS", "OPENS", "CLOSES", "EXP", "DTE", "STOCK", "STOCKS", "SHARE", "SHARES", "TICKER", "TICKERS",
        "SYMBOL", "SYMBOLS", "DESK", "DESKS", "MARKET", "MARKETS", "CHATS", "VIEWS", "SETUP", "SETUPS", "ACTION", "ACTIONS",
        "REPORT", "REPORTS", "RESEARCH", "MONTE", "CARLO", "DATA", "WINDOW", "CHAIN", "CHAINS", "GREEKS", "OPTION", "OPTIONS",
        "EXACT", "EXECUTION", "TODAY", "RIGHT", "SPOT", "SPOTS", "TRIM", "TRAIL"
    }
    q_words = re.findall(r'[A-Za-z0-9&]+', q_clean)
    n = len(q_words)
    for length in [3, 2, 1]:
        for i in range(n - length + 1):
            phrase = ' '.join(q_words[i:i + length]).upper()
            if length == 1 and phrase in stopwords_single:
                continue
            if phrase in _DYNAMIC_NAME_TO_TICKER:
                sym = _DYNAMIC_NAME_TO_TICKER[phrase]
                if sym not in detected:
                    detected.append(sym)
                    matched_by[sym] = "alias"
                    # Mask phrase out of q_clean so it doesn't trigger secondary keyword matches
                    q_clean = re.sub(rf'\b{re.escape(phrase)}\b', ' ', q_clean, flags=re.IGNORECASE)

    # 6. Check standalone ticker tokens excluding market terminology & common English words
    acronym_blacklist = {
        "ARE", "ALL", "NOW", "CAN", "SEE", "FOR", "ON", "IT", "SO", "A", "GO", "BE", "AM", "HAS", "DO",
        "ORB", "EMA", "SMA", "VWAP", "AVWAP", "GEX", "RVOL", "DTE", "ATM", "ITM", "OTM", "ROI", "PNL",
        "CALL", "PUT", "LONG", "SHORT", "STOP", "LOSS", "ZONE", "RISK", "RR", "MAX", "MIN", "HIGH",
        "LOW", "OPEN", "LAST", "CLOSE", "NEWS", "MATH", "DESK", "WEEK", "TIME", "DATE", "SELL", "BUY",
        "GAIN", "CHART", "WHAT", "WHEN", "WHY", "HOW", "SHOW", "TELL", "VIEW", "GOOD", "BEST", "NEXT",
        "HOLD", "MOVE", "PLAY", "RULE", "TEST", "TRUE", "FREE", "MORE", "WITH", "FROM", "THIS", "THAT",
        "PRICE", "PROVIDE", "GIVE", "GET", "CHECK", "REPORT", "FACTORS", "FACTOR", "LEVELS", "LEVEL",
        "SETUP", "SETUPS", "ENTRY", "TARGET", "TRADE", "TRADES", "QUOTES", "QUOTE", "BREAKDOWN", "MODEL",
        "ALSO", "WELL", "LIKE", "SAME", "BOTH", "EACH", "INTO", "OVER", "UNDER", "NEAR", "SIDE", "DAYS",
        "AS", "IF", "AT", "BY", "IN", "IS", "MY", "NO", "OR", "TO", "UP", "US", "WE", "AN", "HE", "ME",
        "OF", "AND", "THE", "BUT", "NOT", "YOU", "ANY", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "DAY",
        "HIM", "HIS", "MAN", "NEW", "OLD", "TWO", "WAY", "WHO", "DID", "ITS", "LET", "PUT", "SAY", "SHE",
        "TOO", "USE", "VERY", "MUCH", "LESS", "THEN", "THAN", "THEM", "THEY", "SOME", "SUCH", "EVEN",
        "MOST", "ONLY", "BEEN", "HAVE", "WERE", "WILL", "JUST", "MAKE", "KNOW", "TAKE", "COME", "LOOK",
        "REAL", "WHERE", "WHICH", "WHILE", "STOCK", "STOCKS", "TICKER", "TICKERS", "SYMBOL", "SYMBOLS",
        "CHAIN", "CHAINS", "PEER", "PEERS", "SECTOR", "VERSUS", "VS", "CORRELATION", "CORRELATED",
        "UI", "UX", "AI", "ML", "NA", "AN", "OK", "CC", "CSP", "BCS", "BPS", "IC", "PE", "EPS", "EV", "FCF",
        "API", "APP", "OS", "FED", "FOMC", "CPI", "PPI", "GDP", "NFP", "PMI", "SEC", "OTC", "EOD", "RTH",
        "EXAMPLE", "EXAMPLES", "LATEST", "COVERED", "SELLING", "BUYING", "HOLDING", "OPTION", "OPTIONS",
        "PATH", "WAYS", "NEED", "WANT", "LIKE", "THINK", "WONDER", "WONDERING", "ABOUT", "COULD", "WOULD", "SHOULD",
        "STEP", "STEPS", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
        "CALLS", "PUTS", "LEAP", "LEAPS", "STRIKE", "STRIKES", "BOUGHT", "SOLD", "EXACT", "EXECUTION", "TODAY", "RIGHT",
        "SPOT", "SPOTS", "TRIM", "TRIMS", "TRAIL", "TRAILS", "SL", "TP"
    }
    tokens = re.findall(r'\b[A-Za-z]{3,5}\b', q_clean)
    for t in tokens:
        tu = t.upper()
        if tu in known and tu not in acronym_blacklist and tu not in detected:
            detected.append(tu)
            matched_by[tu] = "standalone"

    # 7. Inherit active conversation ticker if none detected in prompt
    if not detected and history:
        for turn in reversed(history):
            content = turn.get("content", "")
            role = turn.get("role", "")
            if role == "user":
                prior = _detect_tickers_from_prompt(content)
                filtered = [t for t in prior if t != "GENERAL"]
                if filtered:
                    detected.extend(filtered)
                    for sym in filtered:
                        matched_by[sym] = "history"
                    break
        if not detected:
            for turn in reversed(history):
                content = turn.get("content", "")
                role = turn.get("role", "")
                if role == "assistant":
                    prior = _detect_tickers_from_prompt(content[:400])
                    filtered = [t for t in prior if t != "GENERAL"]
                    if filtered:
                        detected.extend(filtered[:1])
                        matched_by[filtered[0]] = "history"
                        break

    final_tickers = detected if detected else ["GENERAL"]
    primary = final_tickers[0]
    match_type = matched_by.get(primary, "none")
    is_explicit = (match_type in ("dollar", "modal", "keyword"))
    is_inferred = (primary != "GENERAL" and not is_explicit)

    return {
        "tickers": final_tickers,
        "primary_ticker": primary,
        "is_explicit": is_explicit,
        "is_inferred": is_inferred,
        "match_type": match_type,
        "company_name": _get_company_name(primary) if primary != "GENERAL" else "Broad Market",
    }


def _detect_tickers_from_prompt(question: str, explicit_ticker: str = None, history: list = None) -> List[str]:
    """
    Extracts all tickers mentioned in prompt and/or passed explicitly.
    Returns a deduplicated list of uppercase ticker symbols (e.g. ['EIX', 'PCG']).
    """
    return _detect_ticker_metadata(question, explicit_ticker=explicit_ticker, history=history)["tickers"]


def _detect_ticker_from_prompt(question: str, explicit_ticker: str = None, history: list = None) -> str:
    """Legacy single-ticker helper: returns primary detected ticker."""
    tickers = _detect_tickers_from_prompt(question, explicit_ticker, history=history)
    return tickers[0] if tickers else "GENERAL"


def _launch_background_job(name: str, command: list, log_file: str = None) -> str:
    """Spawns an asynchronous background process tracked in SQLite active_research_jobs."""
    import uuid
    _init_db()
    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log_path = LOGS_DIR / (log_file or f"{job_id}.log")
    
    with _get_db() as conn:
        conn.cursor().execute("""
            INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file)
            VALUES (?, ?, 'scrape_only', ?, 'SCRAPING', 'RUNNING', ?, ?)
        """, (job_id, name, os.getpid(), datetime.now(timezone.utc).isoformat(), str(log_path)))
        conn.commit()

    def _worker():
        try:
            _append_log(f"🚀 [{name}] Starting background job {job_id}...")
            p = subprocess.Popen(command, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            ACTIVE_RESEARCH_SUBPROCS[job_id] = p
            with _get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (p.pid, job_id))
                conn.commit()
            with open(log_path, "a", encoding="utf-8") as lf:
                for line in p.stdout:
                    l = line.strip()
                    if l:
                        _append_log(f"[{name}] {l}")
                        lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] {l}\n")
            p.wait()
            status = "COMPLETED" if p.returncode == 0 else "FAILED"
            with _get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_research_jobs SET status = ?, stage = 'DONE', completed_at = ? WHERE job_id = ?",
                    (status, datetime.now(timezone.utc).isoformat(), job_id)
                )
                conn.commit()
            _append_log(f"✅ [{name}] Finished (Exit code: {p.returncode}).")
        except Exception as e:
            _append_log(f"❌ [{name}] Job error: {e}")
            with _get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = ?, completed_at = ? WHERE job_id = ?",
                    (str(e), datetime.now(timezone.utc).isoformat(), job_id)
                )
                conn.commit()
        finally:
            ACTIVE_RESEARCH_WORKERS.pop(job_id, None)
            ACTIVE_RESEARCH_SUBPROCS.pop(job_id, None)

    t = threading.Thread(target=_worker, daemon=True)
    ACTIVE_RESEARCH_WORKERS[job_id] = t
    t.start()
    return job_id


def _build_events_past_chat_context(ticker_u: str, date_str: str, history: list = None, session_id: str = None) -> str:
    """
    Builds a chronological bridge context of what occurred PAST a prior chat / research dossier:
    1. Calendar & elapsed time anchor (today vs prior conversation/dossier).
    2. Spot price evolution (prior reference spot vs live quote, delta, % change).
    3. Status of prior key levels (reclaim pivots, support floors, entry zones).
    4. Macro events & catalysts (e.g. NFP jobs report status today vs 'tomorrow').
    5. Breaking news & headlines published past that chat.
    6. Watchlist & portfolio updates since that chat.
    """
    if not ticker_u or ticker_u in ("GENERAL", "AUTO", "NONE", ""):
        return ""

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    now_et = datetime.now(ZoneInfo("America/New_York"))
    calendar_today = now_mt.strftime("%Y-%m-%d")
    now_str = now_mt.strftime("%A, %b %d, %Y %I:%M %p MT") + f" ({now_et.strftime('%I:%M %p ET')})"

    ref_date = date_str or calendar_today
    is_prior_date = (ref_date < calendar_today)
    has_prior_chat = bool(history and len(history) > 0)

    if not is_prior_date and not has_prior_chat:
        return ""

    try:
        d1 = datetime.strptime(ref_date, "%Y-%m-%d")
        d2 = datetime.strptime(calendar_today, "%Y-%m-%d")
        elapsed_days = (d2 - d1).days
    except Exception:
        elapsed_days = 1 if is_prior_date else 0

    elapsed_str = f"{elapsed_days} calendar day(s)" if elapsed_days > 1 else ("yesterday" if elapsed_days == 1 else "earlier today")

    # 1. Extract reference price and key levels from prior assistant turns or files
    prior_spot = None
    prior_levels = {}
    prior_next_step = ""

    if history:
        for turn in reversed(history):
            if turn.get("role") == "assistant":
                txt = turn.get("content", "")

                # Extract prior spot
                m_spot = re.search(r"(?:Live Spot|Spot|Latest Spot|at market):\*?\*?\s*\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_spot and prior_spot is None:
                    try:
                        prior_spot = float(m_spot.group(1))
                    except Exception:
                        pass

                # Extract Next Step or conditional expectation (ignoring buttons)
                m_next = re.search(r"(?:Next Step|Action|Watch Today):\*?\*?\s*(.*?)(?=\n\n|\Z)", txt, re.DOTALL | re.IGNORECASE)
                if m_next and not prior_next_step:
                    raw_step = " ".join(m_next.group(1).split())
                    clean_step = re.sub(r'\[.*?\]\(action:.*?\)', '', raw_step).strip()
                    if clean_step and len(clean_step) > 10:
                        prior_next_step = clean_step[:280]

                # Extract key levels mentioned
                m_reclaim = re.search(r"reclaims?\s*\*?\*?\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_reclaim and "reclaim" not in prior_levels:
                    prior_levels["reclaim"] = float(m_reclaim.group(1))

                m_supp = re.search(r"(?:Support|Floor):\*?\*?\s*\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_supp and "support" not in prior_levels:
                    prior_levels["support"] = float(m_supp.group(1))

                m_res = re.search(r"Resistance:\*?\*?\s*\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_res and "resistance" not in prior_levels:
                    prior_levels["resistance"] = float(m_res.group(1))

                m_stop = re.search(r"(?:Stop Loss|Hard stop):\*?\*?\s*(?:remains at\s*)?\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_stop and "stop" not in prior_levels:
                    prior_levels["stop"] = float(m_stop.group(1))

                if prior_spot:
                    break

    # Fallback to raw artifacts from ref_date if prior_spot not in chat text
    if prior_spot is None and is_prior_date:
        raw_levels = config.BASE_DIR / "data" / "raw" / ref_date / ticker_u / f"{ticker_u}_watch_levels.json"
        if raw_levels.exists():
            try:
                wl = json.loads(raw_levels.read_text(encoding="utf-8"))
                sp = wl.get("shares_plan", {})
                prior_levels.setdefault("entry_low", sp.get("entry_zone_low"))
                prior_levels.setdefault("entry_high", sp.get("entry_zone_high"))
                prior_levels.setdefault("stop", sp.get("tactical_stop"))
                prior_levels.setdefault("target_1", sp.get("target_1"))
                if wl.get("last_price"):
                    prior_spot = float(wl["last_price"])
            except Exception:
                pass

        if prior_spot is None:
            raw_q = config.BASE_DIR / "data" / "raw" / ref_date / ticker_u / f"{ticker_u}_quote.json"
            if raw_q.exists():
                try:
                    q_data = json.loads(raw_q.read_text(encoding="utf-8"))
                    m_lp = re.search(r"Last:\s*([0-9]+\.[0-9]+)", q_data.get("quote", ""))
                    if m_lp:
                        prior_spot = float(m_lp.group(1))
                except Exception:
                    pass

    # 2. Get Live Current Price NOW
    from src.clients.price_client import get_current_price
    live_price = get_current_price(ticker_u)

    price_delta_lines = []
    if live_price:
        if prior_spot:
            diff = live_price - prior_spot
            diff_pct = (diff / prior_spot) * 100
            sign = "+" if diff >= 0 else ""
            price_delta_lines.append(f"• **Prior Chat / Dossier Reference ({ref_date}):** ${prior_spot:.2f}")
            price_delta_lines.append(f"• **Current Live Spot (NOW):** **${live_price:.2f}** ({sign}${diff:.2f} / {sign}{diff_pct:.2f}% since prior discussion)")
        else:
            price_delta_lines.append(f"• **Current Live Spot (NOW):** **${live_price:.2f}**")

        # Check against prior levels
        if "reclaim" in prior_levels:
            rec_p = prior_levels["reclaim"]
            if live_price >= rec_p:
                price_delta_lines.append(f"• **Pivot Reclaim Status (${rec_p:.2f}):** ✅ **RECLAIMED** (Spot ${live_price:.2f} ≥ ${rec_p:.2f} — breakout path toward resistance opens).")
            else:
                price_delta_lines.append(f"• **Pivot Reclaim Status (${rec_p:.2f}):** ⏳ **NOT RECLAIMED / STALLING BELOW** (Spot ${live_price:.2f} < ${rec_p:.2f} — base building/chop continues).")

        if "support" in prior_levels:
            sup_p = prior_levels["support"]
            if live_price >= sup_p:
                price_delta_lines.append(f"• **Structural Floor Status (${sup_p:.2f}):** 🛡️ **DEFENDED & INTACT** (Spot is holding above the floor).")
            else:
                price_delta_lines.append(f"• **Structural Floor Status (${sup_p:.2f}):** ⚠️ **BREACHED BELOW** (Spot ${live_price:.2f} broke support floor).")

    # 3. Macro & Catalyst Status
    macro_notes = []
    if "2026-09-04" in calendar_today:
        macro_notes.append(
            f"• **US Non-Farm Payrolls (NFP) Catalyst:** Released **THIS MORNING** (Friday, Sep 4 at 8:30 AM ET). "
            f"Any references in yesterday's ({ref_date}) chat or dossier to 'until NFP data tomorrow' are now **PAST** events. "
            "The jobs report has already printed, and the market is digesting the data live today."
        )

    # Fetch live benchmark quotes
    benchmarks = []
    for b_sym in ["SPY", "QQQ", "VIX"]:
        bp = get_current_price(b_sym)
        if bp:
            benchmarks.append(f"{b_sym}: ${bp:.2f}" if b_sym != "VIX" else f"VIX: {bp:.2f}")
    if benchmarks:
        macro_notes.append(f"• **Live Market Regime Today:** {' | '.join(benchmarks)}")

    # 4. Breaking News Past That Chat
    news_lines = []
    try:
        from src.clients.search_client import search_web
        res = search_web(f"{ticker_u} stock news {calendar_today}")
        if res and isinstance(res, list):
            for it in res[:3]:
                t = it.get("title", "").strip()
                b = it.get("body", "").strip()
                if t:
                    news_lines.append(f"• **{t}**\n  {b[:180]}")
    except Exception:
        pass

    # 5. Live SQLite Watch Target status
    watch_lines = []
    try:
        with _get_db() as conn:
            c = conn.cursor()
            row = c.execute("SELECT status, last_price, distance_to_entry_pct, entry_zone_low, entry_zone_high, tactical_stop, target_1 FROM watch_targets WHERE ticker = ?", (ticker_u,)).fetchone()
            if row:
                st = row["status"]
                dist = row["distance_to_entry_pct"]
                dist_str = f" ({dist:+.1f}% to entry)" if dist is not None else ""
                watch_lines.append(f"• **Live Watch Target State:** [{st}]{dist_str} | Entry Zone: ${row['entry_zone_low']}–${row['entry_zone_high']} | Stop: ${row['tactical_stop']} | T1: ${row['target_1']}")
    except Exception:
        pass

    # Build formatted section
    lines = [
        f"### ⚡ LIVE TAPE & CURRENT EVENTS SINCE PRIOR CHAT / RESEARCH ({ref_date} ➔ TODAY {calendar_today}):",
        f"> 🚨 **CHRONOLOGICAL ANCHOR**: The prior chat or research dossier occurred on **{ref_date}** ({elapsed_str}).",
        f"> Current system time is **{now_str}**.",
        f"> Do **NOT** repeat prior-day statements as future expectations (e.g. NFP is **TODAY**, not tomorrow; the previous session's close is in the past).",
        f"> Address the user's question with the live price action, level retests, and current developments that occurred **PAST THAT CHAT**.\n"
    ]

    if price_delta_lines:
        lines.append("#### 📊 Live Spot Price Evolution & Level Retest Status:")
        lines.extend(price_delta_lines)
        lines.append("")

    if prior_next_step:
        lines.append(f"• **Prior Chat Concluding Guidance:** \"{prior_next_step}\"\n")

    if macro_notes:
        lines.append("#### 🌐 Macro & Economic Catalysts Past That Chat:")
        lines.extend(macro_notes)
        lines.append("")

    if watch_lines:
        lines.append("#### 🎯 Active Watchlist Tracking:")
        lines.extend(watch_lines)
        lines.append("")

    if news_lines:
        lines.append(f"#### 📰 Breaking Headlines Since {ref_date}:")
        lines.extend(news_lines)
        lines.append("")

    return "\n".join(lines)


def _extract_targeted_schwab_strikes(ticker: str, question: str) -> Optional[str]:
    """
    Scans the user's question for specific strike mentions (e.g. '245c', '250 puts', 'strike 245', '$250').
    If strikes are detected, queries live Schwab option chain and returns exact Volume, OI, Bid/Ask, Delta, and ITM status.
    """
    if not question or not ticker:
        return None

    # Matches patterns like 245c, 250p, 245 call, 250 puts, strike 245, $250
    pattern = r'(?:^|[^\w\.])(?:strike\s*|\$)?(\d{2,4}(?:\.\d{1,2})?)\s*([cp]|call|put|calls|puts)?\b'
    matches = re.findall(pattern, question, re.IGNORECASE)
    if not matches:
        return None

    requested = []
    for s_str, t_hint in matches:
        try:
            val = float(s_str)
            if val < 5 or val > 2500 or val in (2024, 2025, 2026, 2027, 2028):
                continue
            t_clean = t_hint.lower() if t_hint else None
            side = "CALL" if t_clean in ("c", "call", "calls") else ("PUT" if t_clean in ("p", "put", "puts") else None)
            if (val, side) not in requested:
                requested.append((val, side))
        except ValueError:
            continue

    if not requested:
        return None

    try:
        from src.clients.schwab_client import get_schwab_client
        client = get_schwab_client()
        sym = ticker.upper().strip().replace(".", "/")
        r = client.get_option_chain(sym, contract_type=client.Options.ContractType.ALL)
        if r.status_code != 200:
            return None
        data = r.json()
        underlying = float(data.get("underlyingPrice") or 0.0)

        call_map = data.get("callExpDateMap", {})
        put_map = data.get("putExpDateMap", {})

        # Detect target expiration hints (e.g. 2027, 2028, Oct 2, Sep 25, jan, oct, etc.)
        q_lower = question.lower()
        now_year = datetime.now().year
        year_matches = re.findall(r'\b(202[5-9])\b', q_lower)
        month_matches = re.findall(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b', q_lower)
        month_map = {
            'jan': '01', 'january': '01', 'feb': '02', 'february': '02', 'mar': '03', 'march': '03',
            'apr': '04', 'april': '04', 'may': '05', 'jun': '06', 'june': '06', 'jul': '07', 'july': '07',
            'aug': '08', 'august': '08', 'sep': '09', 'september': '09', 'oct': '10', 'october': '10',
            'nov': '11', 'november': '11', 'dec': '12', 'december': '12'
        }
        target_prefixes = []

        # Check for specific day match e.g. "Oct 2", "Oct 16", "Sep 25"
        day_match = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s*(\d{1,2})\b', q_lower)
        if day_match:
            m_name, d_num = day_match.groups()
            mm = month_map.get(m_name[:3])
            if mm:
                day_year = now_year if int(mm) >= datetime.now().month else (now_year + 1)
                target_prefixes.append(f"{day_year}-{mm}-{int(d_num):02d}")
                if year_matches:
                    for y in year_matches:
                        target_prefixes.append(f"{y}-{mm}-{int(d_num):02d}")

        if year_matches:
            for y in year_matches:
                for m in month_matches:
                    mm = month_map.get(m[:3])
                    if mm:
                        target_prefixes.append(f"{y}-{mm}")
                if not month_matches:
                    target_prefixes.append(f"{y}-")
        elif month_matches:
            for m in month_matches:
                mm = month_map.get(m[:3])
                if mm:
                    target_prefixes.append(f"{now_year}-{mm}")
                    target_prefixes.append(f"{now_year + 1}-{mm}")

        lines = []
        for strike_val, req_side in requested[:6]:
            target_sides = [("CALL", call_map)] if req_side == "CALL" else ([("PUT", put_map)] if req_side == "PUT" else [("CALL", call_map), ("PUT", put_map)])

            for side_name, exp_map in target_sides:
                exp_matched = 0
                for exp_str, strikes in sorted(exp_map.items()):
                    is_targeted_exp = any(tp in exp_str for tp in target_prefixes) if target_prefixes else False
                    if exp_matched >= 3 and not is_targeted_exp:
                        continue
                    if exp_matched >= 10:
                        break
                    for k, contracts in strikes.items():
                        try:
                            if abs(float(k) - strike_val) < 0.01:
                                for c in contracts:
                                    vol = int(c.get("totalVolume") or 0)
                                    oi = int(c.get("openInterest") or 0)
                                    bid = float(c.get("bid") or 0.0)
                                    ask = float(c.get("ask") or 0.0)
                                    delta = round(float(c.get("delta") or 0.0), 3)
                                    itm = bool(c.get("inTheMoney", False))
                                    dte = c.get("daysToExpiration", 0)
                                    exp_date = exp_str.split(":")[0]
                                    ratio_str = f" ({vol/oi:.1f}x OI 🔥)" if oi > 0 and vol > oi * 1.5 else ""
                                    status_str = "IN-THE-MONEY (ITM)" if itm else "OUT-OF-THE-MONEY (OTM)"

                                    lines.append(
                                        f"• **{side_name} ${strike_val:g}** (Exp {exp_date}, {dte}d): "
                                        f"Vol={vol:,} | OI={oi:,}{ratio_str} | Bid=${bid:.2f} Ask=${ask:.2f} | "
                                        f"Delta={delta} | Status: **{status_str}**"
                                    )
                                    exp_matched += 1
                                    break
                        except Exception:
                            continue

        if lines:
            req_str = ", ".join(f"${s:g} {side or ''}".strip() for s, side in requested)
            header = (
                f"### 🎯 TARGETED SCHWAB OPTIONS CHAIN LOOKUP ({ticker} — Live Verified Quotes):\n"
                f"> **Current Spot:** ${underlying:.2f} | **Requested Strikes:** {req_str}\n\n"
            )
            return header + "\n".join(lines)
    except Exception as e:
        logger.debug(f"Targeted Schwab strike query error for {ticker}: {e}")

    return None


def _get_latest_research_date_for_ticker(ticker: str) -> Optional[str]:
    """Find the latest date containing actual research files or raw data for ticker."""
    if not ticker or ticker in ("GENERAL", "AUTO", "NONE", ""):
        return None
    raw_root = config.BASE_DIR / "data" / "raw"
    rep_root = config.BASE_DIR / "reports"
    dates_set = set()
    if raw_root.exists():
        for d in raw_root.glob("202*"):
            t_dir = d / ticker
            if t_dir.is_dir() and any(t_dir.glob(f"{ticker}_*")):
                dates_set.add(d.name)
    if rep_root.exists():
        for d in rep_root.glob("202*"):
            if any(d.glob(f"{ticker}_*")):
                dates_set.add(d.name)
    sorted_d = sorted(list(dates_set), reverse=True)
    return sorted_d[0] if sorted_d else None


_TICKER_CONTEXT_CACHE: Dict[str, Tuple[float, List[str]]] = {}


def _build_single_ticker_context(ticker_u: str, date_str: str, question: str, history: list = None, session_id: str = None) -> List[str]:
    """Fetches real-time quotes, options, news, SEC filings, and research reports for a specific ticker."""
    if not ticker_u or ticker_u in ("GENERAL", "AUTO", "NONE", ""):
        return []

    # Fast in-memory cache (TTL: 30s per ticker & query profile to eliminate latency on follow-up questions)
    import time
    is_leaps_req = bool(re.search(r'\b(leap|leaps|2027|2028|2029|long term|long-term|multi-year|far out)\b', question, re.IGNORECASE))
    is_schwab_req = bool(re.search(r'\b(schwab|holding|holdings|position|positions|shares|covered|calls|puts|portfolio)\b', question, re.IGNORECASE))
    has_date_req = bool(re.findall(r'\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b', question))
    cache_key = f"{ticker_u}_{date_str}_{is_leaps_req}_{is_schwab_req}_{has_date_req}"
    now_ts = time.time()
    if cache_key in _TICKER_CONTEXT_CACHE:
        cached_ts, cached_parts = _TICKER_CONTEXT_CACHE[cache_key]
        if now_ts - cached_ts < 30.0:
            return list(cached_parts)

    parts = []

    # 1. Resolve Historical Research Reports & Baseline Levels First
    raw_root = config.BASE_DIR / "data" / "raw"
    rep_root = config.BASE_DIR / "reports"
    dates_set = set()
    if raw_root.exists():
        for d in raw_root.glob("202*"):
            t_dir = d / ticker_u
            if t_dir.is_dir() and any(t_dir.glob(f"{ticker_u}_*")):
                dates_set.add(d.name)
    if rep_root.exists():
        for d in rep_root.glob("202*"):
            if any(d.glob(f"{ticker_u}_*")):
                dates_set.add(d.name)

    sorted_hist_dates = sorted(list(dates_set), reverse=True)
    latest_dt = sorted_hist_dates[0] if sorted_hist_dates else (date_str if date_str else None)

    report_spot: Optional[float] = None
    report_date: Optional[str] = latest_dt
    shares_plan: Dict[str, Any] = {}
    options_plan: Dict[str, Any] = {}
    invalidation_rule: Dict[str, Any] = {}
    verdict: str = "STALK"
    conviction: int = 5
    levels_file: Optional[Path] = None
    arb_file: Optional[Path] = None
    ind_file: Optional[Path] = None
    sum_file: Optional[Path] = None

    if latest_dt:
        t_raw_dir = raw_root / latest_dt / ticker_u
        t_rep_dir = rep_root / latest_dt

        # Check watch_levels.json
        levels_cand = t_raw_dir / f"{ticker_u}_watch_levels.json"
        if not levels_cand.exists():
            for od in sorted_hist_dates:
                cand_lvl = raw_root / od / ticker_u / f"{ticker_u}_watch_levels.json"
                if cand_lvl.exists():
                    levels_cand = cand_lvl
                    report_date = od
                    break
        if levels_cand.exists():
            levels_file = levels_cand
            try:
                wl_data = json.loads(levels_file.read_text(encoding="utf-8"))
                shares_plan = wl_data.get("shares_plan") or {}
                options_plan = wl_data.get("options_plan") or {}
                invalidation_rule = wl_data.get("invalidation") or {}
                verdict = wl_data.get("verdict") or verdict
                conviction = wl_data.get("conviction") or conviction
                if wl_data.get("last_price"):
                    report_spot = float(wl_data["last_price"])
            except Exception:
                pass

        # Check arbitration directive
        arb_cand = t_rep_dir / f"{ticker_u}_arbitration.md"
        if not arb_cand.exists():
            for od in sorted_hist_dates:
                cand_arb = rep_root / od / f"{ticker_u}_arbitration.md"
                if cand_arb.exists():
                    arb_cand = cand_arb
                    if not report_date:
                        report_date = od
                    break
        if arb_cand.exists():
            arb_file = arb_cand
            try:
                arb_text = arb_file.read_text(encoding="utf-8")
                if report_spot is None:
                    m_sp = re.search(r'(?:spot price|current spot|spot)\s*(?::|\(|is|\$)\s*\$?([0-9]+\.[0-9]+)', arb_text, re.IGNORECASE)
                    if m_sp:
                        report_spot = float(m_sp.group(1))
                if not shares_plan:
                    m_wl = re.search(r'```json:watch_levels\s*(\{.*?\})\s*```', arb_text, re.DOTALL)
                    if m_wl:
                        try:
                            wl_raw = json.loads(m_wl.group(1))
                            shares_plan = wl_raw.get("shares_plan") or {}
                            options_plan = wl_raw.get("options_plan") or {}
                            invalidation_rule = wl_raw.get("invalidation") or {}
                        except Exception:
                            pass
            except Exception:
                pass

        # Check synthesis and independent files
        sum_cand = t_raw_dir / f"{ticker_u}_gemini_thesis.md"
        if not sum_cand.exists():
            sum_cand = t_rep_dir / f"{ticker_u}_summary.md"
        if sum_cand.exists():
            sum_file = sum_cand

        ind_cand = t_raw_dir / f"{ticker_u}_independent_thesis.md"
        if not ind_cand.exists():
            ind_cand = t_rep_dir / f"{ticker_u}_independent.md"
        if ind_cand.exists():
            ind_file = ind_cand

    # 2. Live Real-Time Market Quote & Exact Numerical Spot Extraction
    quote_str = ""
    try:
        from src.clients.options_client import get_realtime_quote
        quote_str = get_realtime_quote(ticker_u)
    except Exception as qe:
        logger.debug(f"Quote fetch failed for {ticker_u}: {qe}")

    from src.clients.price_client import get_current_price
    live_spot: Optional[float] = None
    bid_str, ask_str = "N/A", "N/A"
    if quote_str:
        m_lp = re.search(r'Last:\s*([0-9]+\.[0-9]+)', quote_str)
        if m_lp:
            try:
                live_spot = float(m_lp.group(1))
            except Exception:
                pass
        m_ba = re.search(r'Bid/Ask:\s*([0-9]+\.[0-9]+)\s*/\s*([0-9]+\.[0-9]+)', quote_str)
        if m_ba:
            bid_str, ask_str = f"${m_ba.group(1)}", f"${m_ba.group(2)}"

    if live_spot is None:
        try:
            live_spot = get_current_price(ticker_u)
        except Exception:
            pass

    # 3. Deterministic Level Evaluation & Status
    ez_low = shares_plan.get("entry_zone_low")
    ez_high = shares_plan.get("entry_zone_high")
    stop_p = shares_plan.get("tactical_stop") or invalidation_rule.get("price_level")
    t1_p = shares_plan.get("target_1")
    breakout_p = shares_plan.get("breakout_level")

    calendar_today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    status_badge = "📊 ACTIVE LIVE TAPE"
    status_desc = f"Live spot: ${live_spot:.2f}" if live_spot else "Awaiting live quote"
    copilot_instruction = ""

    if live_spot:
        if stop_p and live_spot <= stop_p:
            status_badge = "🛑 STOP BREACHED / THESIS INVALIDATED"
            status_desc = f"Live spot (${live_spot:.2f}) has breached the tactical stop / invalidation floor (${stop_p:.2f})."
            copilot_instruction = f"The trade setup is INVALIDATED because live spot (${live_spot:.2f}) is at or below the stop (${stop_p:.2f}). Do not enter long."
        elif t1_p and live_spot >= t1_p:
            status_badge = "🏁 TARGET 1 REACHED"
            status_desc = f"Live spot (${live_spot:.2f}) reached Target 1 (${t1_p:.2f}). Consider taking partial profits."
            copilot_instruction = f"Live spot (${live_spot:.2f}) is in the profit target zone (T1 ${t1_p:.2f}). Advise locking in gains or trailing stop."
        elif ez_low and ez_high and ez_low <= live_spot <= ez_high:
            status_badge = "🎯 IN MANDATED ENTRY ZONE RIGHT NOW"
            status_desc = f"Live spot (${live_spot:.2f}) is INSIDE the suggested entry limit zone [${ez_low:.2f} – ${ez_high:.2f}]. Orders are filling live."
            copilot_instruction = f"The stock is ACTIVELY IN THE ENTRY ZONE (${ez_low:.2f}–${ez_high:.2f}). The pullback to ${ez_high:.2f} has already occurred. Setup is buyable/executable here with stop at ${stop_p or 'tactical stop'}."
        elif ez_low and stop_p and stop_p < live_spot < ez_low:
            dist_to_stop = ((live_spot - stop_p) / stop_p) * 100
            status_badge = "⚡ TESTING POC STRUCTURAL FLOOR (PULLBACK COMPLETE)"
            status_desc = f"Live spot (${live_spot:.2f}) pulled back through the limit ceiling (${ez_high:.2f}) down to the structural floor (+{dist_to_stop:.1f}% above ${stop_p:.2f} stop)."
            copilot_instruction = f"The stock HAS ALREADY PULLED BACK from ${report_spot or 'prior highs'} to ${live_spot:.2f}. It is testing the POC structural floor above the ${stop_p:.2f} stop. DO NOT tell the user to wait for a pullback to ${ez_high:.2f}—the pullback has already completed! Evaluate whether support is defending above ${stop_p:.2f}."
        elif ez_high and live_spot > ez_high:
            dist_above = ((live_spot - ez_high) / ez_high) * 100
            status_badge = f"⏳ STALKING (+{dist_above:.1f}% ABOVE ENTRY ZONE)"
            status_desc = f"Live spot (${live_spot:.2f}) is trading above the entry zone ceiling (${ez_high:.2f}). Awaiting pullback or breakout above ${breakout_p or 'resistance'}."
            copilot_instruction = f"Price (${live_spot:.2f}) is currently {dist_above:.1f}% above the top of the entry zone (${ez_high:.2f}). Await pullback to ${ez_high:.2f} or breakout above ${breakout_p or 'resistance'}."

    elapsed_days_str = ""
    if report_date:
        try:
            d_rep = datetime.strptime(report_date, "%Y-%m-%d")
            d_tod = datetime.strptime(calendar_today, "%Y-%m-%d")
            el = (d_tod - d_rep).days
            elapsed_days_str = f"({el} calendar day{'s' if el != 1 else ''} ago)" if el > 0 else "(today)"
        except Exception:
            pass

    delta_str = ""
    if live_spot and report_spot:
        d_val = live_spot - report_spot
        d_pct = (d_val / report_spot) * 100
        sign = "+" if d_val >= 0 else ""
        delta_str = f"• **NET CHANGE SINCE REPORT:** **{sign}${d_val:.2f} ({sign}{d_pct:.2f}%)**"

    top_card = [
        f"### 🚨 AUTHORITATIVE LIVE REAL-TIME MARKET QUOTE & EXECUTION STATUS ({ticker_u}):",
        f"• **CURRENT LIVE SPOT PRICE (NOW):** **${live_spot:.2f}**" if live_spot else f"• **CURRENT LIVE SPOT PRICE (NOW):** Quote Ingesting",
        f"• **REAL-TIME BID / ASK:** {bid_str} / {ask_str}" if (bid_str != "N/A" and ask_str != "N/A") else "",
        f"• **HISTORICAL REPORT BASELINE:** ${report_spot:.2f} (Compiled on {report_date or 'prior session'} {elapsed_days_str})" if report_spot else "",
    ]
    if delta_str:
        top_card.append(delta_str)
    if ez_low and ez_high:
        top_card.append(f"• **MANDATED ENTRY ZONE:** **${ez_low:.2f} – ${ez_high:.2f}** | **TACTICAL STOP:** **${stop_p:.2f}**" + (f" | **TARGET 1:** **${t1_p:.2f}**" if t1_p else ""))
    top_card.append(f"• **REAL-TIME EXECUTION STATUS:** **{status_badge}**")
    top_card.append(f"• **EXECUTION DETAIL:** {status_desc}")

    if quote_str:
        top_card.append(f"\n```\n{quote_str}\n```")

    top_card.append(f"""
> ⚠️ **MANDATORY INSTRUCTION FOR COPILOT / REV CHAT**:
> 1. The **AUTHORITATIVE LIVE SPOT PRICE** right now is **${live_spot:.2f}** (NOT {f'${report_spot:.2f}' if report_spot else 'historical prices'}).
> 2. Any prices cited in historical dossiers below were recorded on {report_date or 'earlier dates'}. NEVER repeat historical prices as today's live spot!
> 3. {copilot_instruction}
> 4. All trade recommendations, options strikes, delta/gamma risk, and distance to stops MUST be computed from the LIVE SPOT PRICE of **${live_spot:.2f}**.
""")
    parts.append("\n".join(l for l in top_card if l))

    # 1a-ii. Active Intraday Open Position Dossier (data/positions.json)
    try:
        pos_file = config.BASE_DIR / "data" / "positions.json"
        if pos_file.exists():
            pos_dict = json.loads(pos_file.read_text(encoding="utf-8"))
            if ticker_u in pos_dict:
                pos = pos_dict[ticker_u]
                p_side = (pos.get("side") or "LONG").upper()
                p_entry = float(pos.get("entry_price") or pos.get("alert_price") or 0.0)
                p_spot = float(pos.get("current_price") or pos.get("last_price") or p_entry)
                p_pnl = 0.0
                if p_entry > 0:
                    p_pnl = ((p_spot - p_entry) / p_entry * 100) if p_side == "LONG" else ((p_entry - p_spot) / p_entry * 100)
                raw_alert = pos.get("raw_alert") or {}
                p_plan = raw_alert.get("plan") or (f"Entry: ${p_entry:.2f}" if p_entry else "N/A")
                p_wrong = raw_alert.get("wrong_if") or "N/A"
                p_ctx = raw_alert.get("context") or "N/A"
                p_opened = (pos.get("opened_at") or "")[11:19] or "Active"
                p_eval = pos.get("last_eval") or ""
                
                pos_card = [
                    f"### 🚨 ACTIVE OPEN POSITION ON YOUR DESK ({ticker_u}):",
                    f"• **Side**: **{p_side}** | **Entry Price**: **${p_entry:.2f}** | **Live Spot**: **${p_spot:.2f}** | **Unrealized P&L**: **{p_pnl:+.2f}%**",
                    f"• **Opened At**: {p_opened} | **Strategy**: {pos.get('strategy', 'Intraday')}",
                    f"• **Engine Card Plan**: {p_plan}",
                    f"• **Kill Line / Invalidation**: {p_wrong}",
                    f"• **Tape Context**: {p_ctx}",
                ]

                # Evaluate against Exit Veto Engine (skills/exit_management_and_veto.md)
                try:
                    from src.tracking.position_monitor import review_tv_exit
                    exit_decision = review_tv_exit(ticker_u, raw_alert)
                    if exit_decision:
                        e_act = exit_decision.get("action", "EVALUATE")
                        e_rsn = exit_decision.get("reason", "")
                        e_icon = "🛑" if e_act == "CONFIRM_EXIT" else ("🛡️" if e_act == "VETO_HOLD" else "⚡")
                        pos_card.append(f"• **Exit Veto Engine State**: {e_icon} **{e_act}** — {e_rsn}")
                except Exception as ee:
                    logger.debug(f"Exit review check error for {ticker_u}: {ee}")

                if p_eval:
                    pos_card.append(f"\n**Latest Position Monitor Evaluation & Guidance:**\n{p_eval}")
                parts.insert(1, "\n".join(pos_card))
    except Exception as pe:
        logger.debug(f"Position check failed for {ticker_u}: {pe}")

    # 1a-iii. Official Schwab Brokerage Positions & Exposure (data/schwab_portfolio.db)
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_positions
        schwab_all = get_portfolio_positions(search=ticker_u)
        schwab_matches = [
            p for p in schwab_all
            if p.get("underlying_symbol", "").upper() == ticker_u or p.get("symbol", "").upper().startswith(ticker_u)
        ]
        if schwab_matches:
            pos_lines = []
            total_mkt = sum(float(p.get("market_value") or 0.0) for p in schwab_matches)
            total_unreal = sum(float(p.get("unrealized_profit_loss") or 0.0) for p in schwab_matches)
            total_day = sum(float(p.get("day_profit_loss") or 0.0) for p in schwab_matches)
            total_cost = sum(float(p.get("cost_basis") or 0.0) for p in schwab_matches)
            unreal_pct = (total_unreal / total_cost * 100.0) if total_cost > 0 else 0.0

            pos_lines.append(
                f"• **AGGREGATE SCHWAB EXPOSURE:** Market Value: **${total_mkt:,.2f}** | Total Unrealized P&L: **${total_unreal:+,.2f} ({unreal_pct:+.2f}%)** | Day P&L: **${total_day:+,.2f}**"
            )
            pos_lines.append("")
            for p in schwab_matches:
                acct = f"{p.get('account_type', 'ACCT')} ({p.get('account_number_masked', '')})"
                a_type = p.get("asset_type", "EQUITY")
                qty = float(p.get("quantity") or 0.0)
                mkt_val = float(p.get("market_value") or 0.0)
                unreal = float(p.get("unrealized_profit_loss") or 0.0)
                u_pct = float(p.get("unrealized_profit_loss_pct") or 0.0)
                avg_px = float(p.get("average_price") or 0.0)
                curr_px = float(p.get("current_price") or 0.0)
                day_gl = float(p.get("day_profit_loss") or 0.0)

                if a_type == "OPTION":
                    desc = p.get("description") or p.get("symbol")
                    exp = p.get("option_expiration") or "N/A"
                    strike = p.get("option_strike") or 0
                    o_type = p.get("option_type") or "CALL"
                    pos_lines.append(
                        f"• **[SCHWAB OPTION] {qty:+.0f} contract(s)**: `{ticker_u} {exp} ${strike} {o_type}` in **{acct}**\n"
                        f"  - Description: {desc}\n"
                        f"  - Position: {'Long' if qty > 0 else 'Short'} {abs(qty):.0f}x contract(s) | Premium Paid/Basis: **${avg_px:.2f}** | Current Mark: **${curr_px:.2f}**\n"
                        f"  - Market Value: **${mkt_val:,.2f}** | Day P&L: **${day_gl:+,.2f}** | Total Unrealized P&L: **${unreal:+,.2f} ({u_pct:+.1f}%)**"
                    )
                else:
                    pos_lines.append(
                        f"• **[SCHWAB SHARES] {qty:.2f} shares** in **{acct}**\n"
                        f"  - Average Cost Basis: **${avg_px:.2f}/share** | Current Price: **${curr_px:.2f}**\n"
                        f"  - Market Value: **${mkt_val:,.2f}** | Day P&L: **${day_gl:+,.2f}** | Total Unrealized P&L: **${unreal:+,.2f} ({u_pct:+.1f}%)**"
                    )

            schwab_card = [
                f"### 💼 USER'S ACTUAL REAL-TIME SCHWAB BROKERAGE POSITIONS ({ticker_u}):",
                "> ⚠️ **MANDATORY BROKERAGE POSITION INTEGRATION**:",
                f"> The user holds real live positions in **${ticker_u}** in their Schwab brokerage account(s).",
                "> Whenever analyzing this ticker, you MUST incorporate their actual holdings, cost basis, unrealized P&L, and existing options legs into your recommendations (e.g. profit taking, stop protection, covered calls, or adding new legs).",
                "",
            ] + pos_lines
            parts.insert(1, "\n".join(schwab_card))
    except Exception as pe:
        logger.debug(f"Schwab positions check failed for {ticker_u}: {pe}")

    # 1b. Live Tape & Current Events Past Prior Chat / Dossier Date
    try:
        past_events = _build_events_past_chat_context(ticker_u, report_date or date_str, history=history, session_id=session_id)
        if past_events:
            parts.append(past_events)
    except Exception as ee:
        logger.debug(f"Error building past events context for {ticker_u}: {ee}")

    # 2. Live Options Chain & Volatility Metrics
    try:
        from src.clients.options_client import fetch_options_chain_tool
        q_lower = question.lower()
        plan_str = str(options_plan).lower()
        is_put_req = bool(re.search(r'\b(put|puts|bull put|bear put|credit spread|cash secured|csp|\d+p)\b', question, re.I)) or ("put" in plan_str)
        is_call_req = bool(re.search(r'\b(call|calls|bull call|bear call|debit spread|long call|\d+c)\b', question, re.I)) or ("call" in plan_str)

        # Check for specific expiration hints (e.g. Oct 2, Sep 25)
        exp_hint = None
        day_match_opt = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s*(\d{1,2})\b', question, re.I)
        if day_match_opt:
            m_name, d_num = day_match_opt.groups()
            mm_dict = {
                'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
                'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
            }
            mm = mm_dict.get(m_name.lower()[:3])
            if mm:
                now_dt = datetime.now()
                year_cand = now_dt.year
                explicit_yr = re.search(r'\b(202[5-9])\b', question)
                if explicit_yr:
                    year_cand = int(explicit_yr.group(1))
                elif int(mm) < now_dt.month:
                    year_cand += 1
                exp_hint = f"{year_cand}-{mm}-{int(d_num):02d}"

        if is_leaps_req:
            opt_data = fetch_options_chain_tool(ticker=ticker_u, direction="BOTH", min_dte=180, max_dte=750, expiration=exp_hint)
            if opt_data:
                parts.append(f"### 📈 LIVE 2027-2028 LEAPS OPTIONS CHAIN & GREEKS ({ticker_u}):\n{opt_data}")
        else:
            opt_data = fetch_options_chain_tool(ticker=ticker_u, direction="BOTH", min_dte=14, max_dte=60, expiration=exp_hint)
            if opt_data:
                parts.append(f"### 📈 LIVE REAL-TIME UNIFIED OPTIONS CHAIN & GREEKS ({ticker_u}):\n{opt_data}")
    except Exception as oe:
        logger.debug(f"Options chain fetch error for {ticker_u}: {oe}")

    try:
        from src.clients import tastytrade_client
        tt_data = tastytrade_client.get_market_metrics(ticker_u)
        if tt_data:
            ivr = tt_data.get('iv_rank')
            ivp = tt_data.get('iv_percentile')
            hv30 = tt_data.get('historical_volatility_30d')
            hv60 = tt_data.get('historical_volatility_60d')
            hv90 = tt_data.get('historical_volatility_90d')
            liq = tt_data.get('liquidity_rating')
            borrow = tt_data.get('borrow_rate')
            parts.append(f"### 🏛️ TASTYTRADE VOLATILITY & LIQUIDITY METRICS ({ticker_u}):\nIV Rank: {ivr}% | IV Percentile: {ivp}% | 30d HV: {hv30} | 60d HV: {hv60} | 90d HV: {hv90} | Option Liquidity: {liq} Stars | Borrow Rate: {borrow}%")
    except Exception:
        pass

    # 2a. Targeted Strike Specific Lookup (Live Verified Schwab Chain for strikes mentioned in prompt)
    try:
        targeted_block = _extract_targeted_schwab_strikes(ticker_u, question)
        if targeted_block:
            parts.append(targeted_block)
    except Exception as te:
        logger.debug(f"Error extracting targeted strikes for {ticker_u}: {te}")

    # 2b. Schwab Institutional Options Flow & Sweeps
    try:
        from src.clients.schwab_client import get_unusual_options_flow_data
        flow = get_unusual_options_flow_data(ticker_u)
        if flow and flow.get("status") == "ok" and flow.get("anomalies"):
            anom_count = flow.get("total_anomalies_count", 0)
            c_vol = flow.get("total_call_volume", 0)
            p_vol = flow.get("total_put_volume", 0)
            c_prem = flow.get("total_call_premium", 0) / 1e6
            p_prem = flow.get("total_put_premium", 0) / 1e6
            bias = flow.get("sentiment_label", "Balanced / Mixed")
            
            top_lines = []
            for a in flow.get("anomalies", [])[:25]:
                prem_k = f"${a['notional_premium']/1e6:.2f}M" if a['notional_premium'] >= 1e6 else f"${round(a['notional_premium']/1000)}k"
                top_lines.append(f"• {a['type']} ${a['strike']} Exp {a['expiry']} ({a['dte']}d): Vol {a['volume']:,} vs OI {a['open_interest']:,} ({a['vol_to_oi']}x OI) | Delta {a['delta']} | Mid ${a['mid']} | Prem {prem_k}")
            
            flow_summary = (
                f"### 🔥 SCHWAB REAL-TIME INSTITUTIONAL OPTIONS FLOW ({ticker_u}):\n"
                f"• Flow Sentiment: **{bias}**\n"
                f"• Unusual Sweeps Count: {anom_count} ({flow.get('call_sweeps_count',0)} Calls / {flow.get('put_sweeps_count',0)} Puts)\n"
                f"• Call Sweep Volume: {c_vol:,} (${c_prem:.2f}M premium) | Put Sweep Volume: {p_vol:,} (${p_prem:.2f}M premium)\n"
                f"• Put/Call Ratios: {flow.get('put_call_volume_ratio',0)}x Vol | {flow.get('put_call_premium_ratio',0)}x Premium\n"
                f"**Top Institutional Sweeps (Vol > 1.5x OI):**\n" + "\n".join(top_lines)
            )
            parts.append(flow_summary)
    except Exception as fe:
        logger.debug(f"Schwab options flow fetch error for {ticker_u}: {fe}")

    # 3. Verified Python Quantitative Engine Calculations (df 300 bars x 85 indicators)
    try:
        from src.clients.llm_client import execute_python_code_tool
        quant_py = """
if not df.empty:
    close = df['close'].iloc[-1]
    high_52w = df['high'].tail(252).max()
    low_52w = df['low'].tail(252).min()
    sma20 = df['close'].tail(20).mean()
    sma50 = df['close'].tail(50).mean()
    sma200 = df['close'].tail(200).mean() if len(df) >= 200 else None
    
    # 20d Realized Volatility
    returns = df['close'].pct_change().dropna()
    hv20 = returns.tail(20).std() * np.sqrt(252) * 100
    
    # 14d ATR
    high_low = df['high'] - df['low']
    high_cp = (df['high'] - df['close'].shift()).abs()
    low_cp = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    atr14 = tr.tail(14).mean()
    atr_pct = (atr14 / close) * 100 if close else 0
    
    # 30-day Monte Carlo Probabilities (10,000 paths)
    daily_vol = (hv20 / 100) / np.sqrt(252) if hv20 else 0.015
    np.random.seed(42)
    sim_returns = np.random.normal(0, daily_vol, (10000, 30))
    price_paths = close * np.cumprod(1 + sim_returns, axis=1)
    p_up_5 = np.mean(np.any(price_paths >= close * 1.05, axis=1)) * 100
    p_down_5 = np.mean(np.any(price_paths <= close * 0.95, axis=1)) * 100
    p_up_10 = np.mean(np.any(price_paths >= close * 1.10, axis=1)) * 100

    print(f"• Last Data Window Close: ${close:.2f}")
    print(f"• 52-Week High / Low: ${high_52w:.2f} / ${low_52w:.2f}")
    print(f"• Moving Averages: 20d SMA=${sma20:.2f} | 50d SMA=${sma50:.2f}" + (f" | 200d SMA=${sma200:.2f}" if sma200 else ""))
    print(f"• 14d Average True Range (ATR): ${atr14:.2f} ({atr_pct:.2f}% of spot)")
    print(f"• 20d Realized Volatility (HV20): {hv20:.1f}%")
    print(f"• 30-Day Monte Carlo 10,000-Path Probabilities:")
    print(f"  - P(Touch +5% Gain at ${close*1.05:.2f}): {p_up_5:.1f}%")
    print(f"  - P(Touch -5% Drop at ${close*0.95:.2f}): {p_down_5:.1f}%")
    print(f"  - P(Touch +10% Gain at ${close*1.10:.2f}): {p_up_10:.1f}%")
"""
        quant_out = execute_python_code_tool(quant_py, ticker=ticker_u, date_str=date_str)
        if quant_out and "Error" not in quant_out and "No local" not in quant_out:
            parts.append(f"### 🐍 VERIFIED PYTHON QUANTITATIVE ANALYTICS & MATH ({ticker_u}):\n{quant_out}")
    except Exception as pe:
        logger.debug(f"Python quantitative baseline error for {ticker_u}: {pe}")

    # Check for user-supplied Python code in question
    code_matches = re.findall(r'```(?:python)?\s*(.*?)\s*```', question, re.DOTALL)
    if not code_matches:
        if any(kw in question.lower() for kw in ["execute python", "run python", "python:", "eval python", "calculate:", "compute:"]):
            py_match = re.search(r'(?:python|calculate|compute):\s*(.+)', question, re.IGNORECASE | re.DOTALL)
            if py_match:
                code_matches = [py_match.group(1)]

    if code_matches:
        for user_code in code_matches:
            try:
                from src.clients.llm_client import execute_python_code_tool
                exec_out = execute_python_code_tool(user_code, ticker=ticker_u, date_str=date_str)
                parts.append(f"### 🐍 USER PYTHON CODE EXECUTION RESULTS (Deterministic Sandbox):\n```python\n{user_code}\n```\n**STDOUT Output:**\n```\n{exec_out}\n```")
            except Exception as upe:
                parts.append(f"### 🐍 USER PYTHON EXECUTION ERROR:\n{upe}")

    # 4. SEC EDGAR Audit & Financial Facts (On-Demand or when fundamentals asked)
    is_fundamental_req = bool(re.search(r'\b(sec|edgar|10-k|10-q|filing|balance sheet|debt|revenue|fundamental|cash flow)\b', question, re.I))
    if is_fundamental_req:
        try:
            from src.clients.sec_edgar_client import format_sec_report
            sec_audit = format_sec_report(ticker_u, limit=3)
            if sec_audit and "Error" not in sec_audit and "Could not resolve" not in sec_audit:
                parts.append(sec_audit)
        except Exception as se:
            logger.debug(f"SEC EDGAR fetch error for {ticker_u}: {se}")

    # 5. Live Breaking News & Web Search Catalysts (Triggered upfront if news/catalyst queried, or via LLM search_web tool)
    is_news_req = bool(re.search(r'\b(news|catalyst|headline|rumor|event|article|why\b|drop|rip|fell|jumped|data center|deal|partnership|earnings)\b', question, re.I))
    if is_news_req:
        try:
            live_news_items = []
            from src.clients.search_client import search_web
            search_results = search_web(f"{ticker_u} stock news earnings catalysts latest")
            if search_results and isinstance(search_results, list):
                for item in search_results[:3]:
                    title = item.get("title", "").strip()
                    body = item.get("body", "").strip()
                    href = item.get("href", "").strip()
                    if title:
                        live_news_items.append(f"• **{title}**\n  {body}\n  Source: {href}")

            import yfinance as yf
            yf_ticker = yf.Ticker(ticker_u)
            yf_news = getattr(yf_ticker, "news", [])
            if yf_news and isinstance(yf_news, list):
                for item in yf_news[:4]:
                    content_obj = item.get("content", {})
                    title = content_obj.get("title") or item.get("title")
                    summary = content_obj.get("summary") or item.get("summary") or ""
                    pub_date = content_obj.get("pubDate") or item.get("providerPublishTime") or ""
                    if title and not any(title[:30].lower() in x.lower() for x in live_news_items):
                        live_news_items.append(f"• **{title}** ({pub_date})\n  {summary[:200]}")

            if live_news_items:
                calendar_now = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d %H:%M MT")
                parts.append(
                    f"### 📰 TODAY'S REAL-TIME BREAKING NEWS ({ticker_u} — Retrieved {calendar_now}):\n"
                    f"> ⚠️ **TEMPORAL BOUNDARY**: The headlines below occurred TODAY ({calendar_now[:10]}). "
                    f"They were **NOT** available when historical dossiers (e.g. earlier dates) were compiled. "
                    f"Never claim an earlier research report 'had notice' of breaking news that was published after that report's date.\n\n"
                    + "\n\n".join(live_news_items[:5])
                )
        except Exception as ne:
            logger.debug(f"Live news search fetch error for {ticker_u}: {ne}")

    # 5. Local Filesystem Research Reports
    try:
        if latest_dt:
            spot_comp_str = f" (Spot at publication was ${report_spot:.2f} vs LIVE SPOT ${live_spot:.2f} right now)" if (report_spot and live_spot) else ""

            # Independent Quantitative Study (Model B)
            if ind_file and ind_file.exists():
                parts.append(
                    f"### 🧠 LATEST INDEPENDENT QUANTITATIVE THESIS ({ticker_u} — Date: {report_date}):\n"
                    f"> ⚠️ **HISTORICAL DOSSIER FROM {report_date}**{spot_comp_str}:\n"
                    f"> The thesis below reflects technical patterns as of {report_date}. Do NOT confuse historical prices with today's live tape.\n\n"
                    + ind_file.read_text(encoding='utf-8')[:4000]
                )

            # Synthesis Report (Model A)
            if sum_file and sum_file.exists():
                parts.append(
                    f"### 🔬 LATEST MULTI-MODEL SYNTHESIS DOSSIER ({ticker_u} — Date: {report_date}):\n"
                    f"> ⚠️ **HISTORICAL DOSSIER FROM {report_date}**{spot_comp_str}:\n\n"
                    + sum_file.read_text(encoding='utf-8')[:3500]
                )

            # Senior PM Arbitration Directive
            if arb_file and arb_file.exists():
                parts.append(
                    f"### ⚖️ SENIOR PM ARBITRATION DIRECTIVE ({ticker_u} — COMPILED ON {report_date}):\n"
                    f"> ⚠️ **HISTORICAL ARBITRATION DIRECTIVE FROM {report_date}**{spot_comp_str}:\n"
                    f"> Any mention of 'current spot' in the text below refers to {report_date} (${report_spot:.2f}). "
                    f"Today's live price is **${live_spot:.2f}**. Never quote the historical spot as current price!\n\n"
                    + arb_file.read_text(encoding='utf-8')[:3500]
                )

            # Tactical Watch Levels
            if levels_file and levels_file.exists():
                try:
                    parts.append(f"### 🎯 STRUCTURED TACTICAL WATCH LEVELS ({ticker_u} — Base Date {report_date}):\n{levels_file.read_text(encoding='utf-8')}")
                except Exception:
                    pass
    except Exception as he:
        logger.debug(f"Historical research search failed for {ticker_u}: {he}")

    if parts:
        _TICKER_CONTEXT_CACHE[cache_key] = (time.time(), parts)

    return parts


def _build_daily_overview_context(date_str: Optional[str] = None, question: str = "") -> Tuple[str, List[str]]:
    """
    Assembles a high-level executive briefing and desk overview of:
    1. Active research date discovery (resolving requested date vs latest research run).
    2. Deep research pipeline jobs run today/recently (completed/running tickers).
    3. Senior PM arbitration directives & theses (verdict, conviction, entry zone, stops, targets, options structure, invalidation rule, and judge's conflict resolution).
    4. Active watchlist & trigger states (IN_ZONE, STALKING, IN_TRADE, TARGET_HIT, INVALIDATED).
    5. Open positions and portfolio status (side, entry, spot, P&L, status).
    6. Market benchmark indices & volatility regime (SPY, QQQ, IWM, VIX).
    7. Synthesized desk gameplan: what was run, what was learned, and what we should do today.
    """
    parts = []
    today_tickers: List[str] = []
    
    calendar_today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    rep_root = config.BASE_DIR / "reports"
    raw_root = config.BASE_DIR / "data" / "raw"
    all_dates = []
    if rep_root.exists():
        all_dates = sorted([d.name for d in rep_root.glob("202*") if d.is_dir() and any(d.glob("*_summary.md"))], reverse=True)
    
    # Resolve active research date
    if date_str and (rep_root / date_str).exists() and any((rep_root / date_str).glob("*_summary.md")):
        active_date = date_str
    elif all_dates:
        active_date = all_dates[0]
    else:
        active_date = date_str or calendar_today

    date_header = f"Research Date: {active_date}"
    if active_date != calendar_today:
        date_header += f" (Calendar Today: {calendar_today})"
        
    parts.append(f"# 🏛️ INSTITUTIONAL DAILY RESEARCH BRIEFING & DESK OVERVIEW\n**{date_header}**")

    if active_date != calendar_today:
        now_mt = datetime.now(ZoneInfo("America/Denver"))
        now_et = datetime.now(ZoneInfo("America/New_York"))
        now_str = now_mt.strftime("%A, %b %d, %Y %I:%M %p MT") + f" ({now_et.strftime('%I:%M %p ET')})"
        interim_lines = [
            f"> 🚨 **TEMPORAL BRIDGE**: Underlying research reports below were compiled on **{active_date}**, but current market time is **{now_str}**.",
            "> Do NOT speak of events that were scheduled between that research date and today as upcoming (e.g. NFP on Sep 4 was released this morning at 8:30 AM ET).",
            f"> The desk is currently executing live trading off the {active_date} baseline with real-time level retests and trigger surveillance."
        ]
        parts.append("\n".join(interim_lines))

    # Pre-populate cached prices from SQLite watch_targets and positions.json for instant resolution
    cached_prices: Dict[str, float] = {}
    try:
        with _get_db() as conn:
            c = conn.cursor()
            for r in c.execute("SELECT ticker, last_price FROM watch_targets WHERE last_price IS NOT NULL").fetchall():
                try:
                    if r["last_price"]:
                        cached_prices[r["ticker"].upper()] = float(r["last_price"])
                except Exception:
                    pass
    except Exception:
        pass

    try:
        pos_file = config.BASE_DIR / "data" / "positions.json"
        if pos_file.exists():
            with open(pos_file, "r", encoding="utf-8") as pf:
                pos_data = json.load(pf)
                pos_dict = pos_data.get("positions", pos_data) if isinstance(pos_data, dict) else {}
                for sym, p in pos_dict.items():
                    if isinstance(p, dict) and p.get("last_price"):
                        try:
                            cached_prices[sym.upper()] = float(p["last_price"])
                        except Exception:
                            pass
    except Exception:
        pass

    # 1. Pipeline Execution & Jobs Run Today
    try:
        with _get_db() as conn:
            c = conn.cursor()
            jobs = c.execute(
                "SELECT ticker, mode, status, started_at, completed_at FROM active_research_jobs "
                "WHERE started_at LIKE ? OR started_at LIKE ? OR started_at >= datetime('now', '-24 hours') "
                "ORDER BY rowid DESC LIMIT 15",
                (f"{calendar_today}%", f"{active_date}%")
            ).fetchall()
            
            if jobs:
                job_lines = []
                seen_jobs = set()
                for j in jobs:
                    sym = (j["ticker"] or "").upper()
                    st = j["status"]
                    mode = (j["mode"] or "full").upper()
                    key = f"{sym}_{st}_{mode}"
                    if key in seen_jobs:
                        continue
                    seen_jobs.add(key)
                    if sym and sym not in today_tickers:
                        today_tickers.append(sym)
                    
                    time_info = ""
                    if j["started_at"]:
                        try:
                            t_str = j["started_at"].split("T")[1][:5] if "T" in j["started_at"] else j["started_at"][-8:-3]
                            time_info = f" at {t_str} UTC"
                        except Exception:
                            pass
                    job_lines.append(f"• **{sym}**: {mode} research pipeline — status: **{st}**{time_info}")
                if job_lines:
                    parts.append("### ⚙️ RESEARCH PIPELINES RUN TODAY:\n" + "\n".join(job_lines))
    except Exception as je:
        logger.debug(f"Error querying research jobs: {je}")

    # Check survivors.json for active date
    try:
        surv_file = config.BASE_DIR / "data" / "raw" / active_date / "survivors.json"
        if surv_file.exists():
            with open(surv_file, "r", encoding="utf-8") as sf:
                surv_data = json.load(sf)
                if isinstance(surv_data, list):
                    surv_syms = [s.get("Symbol") or s.get("Ticker") for s in surv_data if isinstance(s, dict)]
                    surv_syms = [s.upper() for s in surv_syms if s]
                    for s in surv_syms:
                        if s not in today_tickers:
                            today_tickers.append(s)
                    if surv_syms:
                        parts.append(f"### 📋 SCREENER SURVIVOR CANDIDATES ({active_date}):\nActive Screened Universe: **{', '.join(surv_syms)}**")
    except Exception:
        pass

    # 2. Senior PM Arbitration Directives & Theses
    try:
        rep_dir = rep_root / active_date
        if rep_dir.exists():
            arb_files = sorted(list(rep_dir.glob("*_arbitration.md")))
            dossier_lines = []
            for af in arb_files:
                sym = af.name.replace("_arbitration.md", "").upper()
                if sym not in today_tickers:
                    today_tickers.append(sym)
                
                txt = af.read_text(encoding="utf-8")
                
                # Extract watch_levels json
                wl = {}
                m_wl = re.search(r"```json:watch_levels\s*(\{.*?\})\s*```", txt, re.DOTALL)
                if m_wl:
                    try:
                        wl = json.loads(m_wl.group(1))
                    except Exception:
                        pass
                if not wl:
                    for cand_p in [
                        raw_root / active_date / sym / f"{sym}_watch_levels.json",
                        config.BASE_DIR / "data" / "triage" / active_date / "_DEEP_RESEARCH" / sym / f"{sym}_watch_levels.json",
                        config.BASE_DIR / "data" / "triage" / active_date / "force" / sym / f"{sym}_watch_levels.json",
                    ]:
                        if cand_p.exists():
                            try:
                                wl = json.loads(cand_p.read_text(encoding="utf-8"))
                                break
                            except Exception:
                                pass
                
                # Extract verdict & conviction
                verdict = wl.get("verdict")
                conviction = wl.get("conviction")
                if not verdict or verdict == "ANALYZED":
                    vm = re.search(r"(?:Final Verdict|\*\*Ruling|\*\*Verdict):\*?\*?\s*([A-Za-z_]+(?:\s*\([^\)]+\))?)", txt, re.IGNORECASE)
                    if vm:
                        verdict = vm.group(1).strip()
                    else:
                        verdict = "STALK"
                if conviction is None:
                    cm = re.search(r"Conviction:\s*(\d+)/10", txt, re.IGNORECASE)
                    conviction = int(cm.group(1)) if cm else 5

                # Cached spot price
                spot_price = cached_prices.get(sym)
                spot_str = f" | Current Spot: **${spot_price:.2f}**" if spot_price else ""

                shares_plan = wl.get("shares_plan") or {}
                options_plan = wl.get("options_plan") or {}

                # Extract Judge's Ruling / Conflict Resolution
                ruling_snippet = ""
                r_match = re.search(r"## ⚖️ THE JUDGE'S FINAL RULING\s*(.*?)(?=## 🎯|\Z)", txt, re.DOTALL)
                if r_match:
                    clean_r = " ".join(r_match.group(1).split())
                    ruling_snippet = clean_r[:420] + ("..." if len(clean_r) > 420 else "")

                # Extract The ONE Thing Invalidation
                invalidation_snippet = ""
                inv_match = re.search(r"\*\*The ONE Thing Invalidation:\*\*\s*(.*?)(?=\n\n|\Z|```)", txt, re.DOTALL)
                if inv_match:
                    invalidation_snippet = " ".join(inv_match.group(1).split())[:200]
                elif wl.get("invalidation_condition"):
                    invalidation_snippet = wl["invalidation_condition"]

                card = [f"#### 📌 **{sym}** — Verdict: **{verdict}** (Conviction: {conviction}/10){spot_str}"]
                
                if shares_plan:
                    ez_l = shares_plan.get("entry_zone_low")
                    ez_h = shares_plan.get("entry_zone_high")
                    stop = shares_plan.get("tactical_stop")
                    t1 = shares_plan.get("target_1")
                    t2 = shares_plan.get("target_2")
                    card.append(f"  - **Shares Tactical Plan:** Limit Entry Zone: **${ez_l} – ${ez_h}** | Stop: **${stop}** | Target 1: **${t1}** | Target 2: **${t2}**")
                
                if options_plan:
                    opt_sum = options_plan.get("summary") or options_plan.get("structure")
                    opt_exp = options_plan.get("expiration")
                    opt_long = options_plan.get("long_strike")
                    opt_short = options_plan.get("short_strike")
                    opt_debit = options_plan.get("target_debit")
                    opt_str = f"  - **Options Vehicle:** {opt_sum}"
                    if opt_exp and opt_long and opt_short:
                        opt_str += f" (Exp: {opt_exp}, {opt_long}/{opt_short} spread, target debit: <${opt_debit})"
                    card.append(opt_str)

                if invalidation_snippet:
                    card.append(f"  - **The ONE Thing Invalidation:** {invalidation_snippet}")

                if ruling_snippet:
                    card.append(f"  - **Judicial Ruling & Conflict Resolution:** {ruling_snippet}")

                dossier_lines.append("\n".join(card))

            if dossier_lines:
                parts.append(f"### ⚖️ SENIOR PM ARBITRATION DIRECTIVES & TACTICAL THESES ({active_date}):\n" + "\n\n".join(dossier_lines))
    except Exception as de:
        logger.debug(f"Error parsing arbitration dossiers: {de}")

    # 3. Active Watchlist & Tactical Triggers
    try:
        with _get_db() as conn:
            c = conn.cursor()
            w_rows = c.execute(
                "SELECT ticker, date, verdict, status, entry_zone_low, entry_zone_high, tactical_stop, target_1, last_price, distance_to_entry_pct "
                "FROM watch_targets ORDER BY "
                "CASE status WHEN 'IN_ZONE' THEN 1 WHEN 'IN_TRADE' THEN 2 WHEN 'STALKING' THEN 3 ELSE 4 END, "
                "distance_to_entry_pct ASC"
            ).fetchall()

            if w_rows:
                in_zone = []
                in_trade = []
                stalking = []
                other_targets = []
                for r in w_rows:
                    sym = r["ticker"]
                    if sym not in today_tickers:
                        today_tickers.append(sym)
                    st = r["status"]
                    dist = r["distance_to_entry_pct"]
                    dist_str = f" ({dist:+.1f}% to entry)" if dist is not None else ""
                    last_p = f"Spot: ${r['last_price']:.2f}" if r["last_price"] else ""
                    line = f"• **{sym}** [{st}]{dist_str} — {last_p} | Entry: ${r['entry_zone_low']}–${r['entry_zone_high']} | Stop: ${r['tactical_stop']} | T1: ${r['target_1']}"
                    
                    if st == "IN_ZONE":
                        in_zone.append(line)
                    elif st == "IN_TRADE":
                        in_trade.append(line)
                    elif st == "STALKING":
                        stalking.append(line)
                    else:
                        other_targets.append(f"• **{sym}** [{st}] — Entry: ${r['entry_zone_low']}–${r['entry_zone_high']}")

                watch_sections = []
                if in_zone:
                    watch_sections.append("🎯 **IN ZONE (Actionable Trigger Ready):**\n" + "\n".join(in_zone))
                if in_trade:
                    watch_sections.append("🚀 **IN TRADE (Active Positions Working):**\n" + "\n".join(in_trade))
                if stalking:
                    watch_sections.append("⏳ **STALKING (Awaiting Pullback / Limit Fill):**\n" + "\n".join(stalking))
                if other_targets:
                    watch_sections.append("🏁 **TARGET HIT / INVALIDATED:**\n" + "\n".join(other_targets))

                parts.append("### 🎯 ACTIVE WATCHLIST & TACTICAL TRIGGERS (Live SQLite Tracking):\n" + "\n\n".join(watch_sections))
    except Exception as we:
        logger.debug(f"Error querying watch targets: {we}")

    # 4. Open Positions & Portfolio State (data/positions.json)
    try:
        pos_file = config.BASE_DIR / "data" / "positions.json"
        if pos_file.exists():
            pos_dict = json.loads(pos_file.read_text(encoding="utf-8"))
            if pos_dict:
                pos_lines = []
                for sym, p in pos_dict.items():
                    side = (p.get("side") or "LONG").upper()
                    entry = float(p.get("entry_price") or p.get("alert_price") or 0.0)
                    spot = float(p.get("current_price") or p.get("last_price") or entry)
                    pnl = 0.0
                    if entry > 0:
                        pnl = ((spot - entry) / entry * 100) if side == "LONG" else ((entry - spot) / entry * 100)
                    raw_a = p.get("raw_alert") or {}
                    plan = raw_a.get("plan") or (f"Entry: ${entry:.2f}" if entry else "")
                    wrong_if = raw_a.get("wrong_if") or ""
                    opened = (p.get("opened_at") or "")[11:19] or "Active"
                    line = f"• **${sym}** ({side}) | Entry: **${entry:.2f}** | Spot: **${spot:.2f}** | P&L: **{pnl:+.2f}%** | Opened: {opened}"
                    try:
                        from src.tracking.position_monitor import review_tv_exit
                        e_rev = review_tv_exit(sym, raw_a)
                        if e_rev:
                            e_act = e_rev.get("action", "")
                            e_rsn = e_rev.get("reason", "")
                            e_icon = "🛑" if e_act == "CONFIRM_EXIT" else ("🛡️" if e_act == "VETO_HOLD" else "⚡")
                            line += f"\n  - Exit Veto Status: {e_icon} **{e_act}** ({e_rsn})"
                    except Exception:
                        pass
                    if plan:
                        line += f"\n  - Plan: {plan}"
                    if wrong_if:
                        line += f" | Invalidation: {wrong_if}"
                    pos_lines.append(line)
                parts.append(f"### 💼 ACTIVE INTRADAY OPEN POSITIONS ({len(pos_lines)} open positions in data/positions.json):\n" + "\n\n".join(pos_lines))
    except Exception as pe:
        logger.debug(f"Error loading positions.json in overview: {pe}")

    # 5. Live Benchmark Quotes & Volatility
    try:
        benchmarks = []
        for b_sym in ["SPY", "QQQ", "IWM", "VIX"]:
            bp = cached_prices.get(b_sym)
            if bp:
                benchmarks.append(f"{b_sym}: ${bp:.2f}")
        if benchmarks:
            parts.append(f"### 🌐 LIVE MARKET BENCHMARKS & VOLATILITY REGIME:\n" + " | ".join(benchmarks))
    except Exception:
        pass

    overview_text = "\n\n".join(parts) if parts else ""
    return overview_text, today_tickers


def _build_copilot_context_and_tools(question: str, explicit_ticker: str = None, date_str: str = None, history: list = None, session_id: str = None, board_context: str = None):
    """
    Executes live data tools and assembles complete quantitative context for Copilot:
    1. Triggers background TradingView scrape if requested in prompt.
    2. Builds daily executive research briefing and desk overview (runs today, PM arbitration, watchlist triggers, open positions).
    3. Fetches multi-tier real-time price quotes (Alpaca / Tastytrade / yfinance) for ALL mentioned tickers.
    4. Fetches live options chains + Tastytrade market metrics (IV Rank, HV, Greeks).
    5. Fetches official SEC EDGAR regulatory filings and balance sheet metrics.
    6. Sandboxed verified Python execution for quantitative analytics (ATRs, Monte Carlo, HV20).
    7. Gathers Senior PM judicial arbitration, Model A synthesis, Model B independent, and watch levels.
    8. Injects chronological bridge of live tape & events that occurred PAST the prior chat / research date.
    9. Injects active board/screen telemetry (exact table rows, active tab, highlighted selection).
    """
    ticker_meta = _detect_ticker_metadata(question, explicit_ticker, history=history)
    detected_tickers = ticker_meta["tickers"]
    has_specific_ticker = any(t for t in detected_tickers if t not in ("GENERAL", "AUTO", "NONE", "ALL", ""))
    primary_ticker = detected_tickers[0] if has_specific_ticker else "GENERAL"
    is_explicit = ticker_meta["is_explicit"]
    company_name = ticker_meta["company_name"]

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    now_et = datetime.now(ZoneInfo("America/New_York"))
    calendar_today = now_mt.strftime("%Y-%m-%d")
    now_str = now_mt.strftime("%A, %b %d, %Y %I:%M %p MT")
    now_et_str = now_et.strftime("%I:%M %p ET")

    if not date_str:
        date_str = calendar_today

    dossier_date = date_str
    if has_specific_ticker and (not explicit_ticker or date_str == calendar_today):
        discovered_date = _get_latest_research_date_for_ticker(primary_ticker)
        if discovered_date:
            dossier_date = discovered_date

    is_prior = (dossier_date < calendar_today)
    try:
        elapsed_days = (datetime.strptime(calendar_today, "%Y-%m-%d") - datetime.strptime(dossier_date, "%Y-%m-%d")).days if is_prior else 0
    except Exception:
        elapsed_days = 1 if is_prior else 0
    elapsed_days_str = f"{elapsed_days} calendar day(s) ago" if elapsed_days > 1 else ("yesterday" if elapsed_days == 1 else "today")

    context_parts = []

    # 0a. Active Screen / Board Telemetry (User's Currently Visible View / Selection)
    if board_context and board_context.strip():
        context_parts.append(
            f"### 📋 USER'S ACTIVE BOARD / SCREEN TELEMETRY (What the User Sees on Screen Right Now):\n"
            f"> 💡 The user is actively viewing this exact data table or screen slice on their dashboard right now. "
            f"Ground your analysis in this visible data:\n\n"
            f"{board_context.strip()}"
        )

    has_explicit_dossier = bool(explicit_ticker and explicit_ticker.strip().upper() not in ("GENERAL", "AUTO", "NONE", "ALL", ""))

    # 0b. Ticker Verification Directive for Inferred / Unconfirmed Symbols
    if has_specific_ticker and not is_explicit and not has_explicit_dossier:
        ticker_confirmation_block = f"""### 🎯 MANDATORY TICKER VERIFICATION & CONFIRMATION DIRECTIVE:
The ticker '${primary_ticker}' was INFERRED from the user's conversational text ("{question}"), NOT from an explicit '$' symbol or active modal selection.
The user may be asking about {company_name} (${primary_ticker}), or they might be speaking colloquially, using a typo, or asking a general question.

You MUST follow this exact structure:
1. Open your response with a prominent, polite Ticker Confirmation Card:
   🎯 **Ticker Check**: I detected **${primary_ticker}** ({company_name}). Is this the stock you would like to analyze?
   [✅ Yes, Analyze ${primary_ticker}](action:ask?prompt=Yes,+analyze+${primary_ticker})  [✏️ Different Ticker](action:ask?prompt=No,+I+meant+$)

2. Provide a concise preliminary answer/quote for ${primary_ticker} based on the live context below.
3. If the user's question was also a general market question (e.g. asking about covered call concepts), answer the core concept as well so the user is never left hanging.
"""
        context_parts.append(ticker_confirmation_block)

    # 1. Trigger fresh scrape if requested by user
    scrape_intent = bool(re.search(r'\b(scrape|rescrap|rescan|fetch chart|fresh chart|new chart|update chart)\b', question, re.IGNORECASE))
    if scrape_intent and primary_ticker and primary_ticker not in ("GENERAL", "AUTO", "NONE", ""):
        try:
            job_id = _launch_background_job(
                name=f"Chart Scrape ({primary_ticker})",
                command=[sys.executable, "run_swing_research.py", "--ticker", primary_ticker, "--force"],
                log_file=f"scrape_{primary_ticker}.log"
            )
            context_parts.append(f"### 🔄 AUTOMATED SCRAPE TRIGGERED FOR {primary_ticker}\nAction Executed: Successfully launched background TradingView chart scraper (Job ID: {job_id}).\nInforming user that a fresh chart and Data Window are currently updating in data/raw/{date_str}/{primary_ticker}/.")
        except Exception as se:
            context_parts.append(f"### 🔄 SCRAPE ATTEMPT\nNotice: Could not launch background scrape: {se}")

    is_single_stock = bool(primary_ticker and primary_ticker not in ("GENERAL", "AUTO", "NONE", "ALL", ""))

    is_modal_or_alert = bool(board_context and any(k in board_context for k in (
        "CURRENT TRADINGVIEW ALERT MODAL CONTEXT",
        "MODEL B INDEPENDENT REPORT",
        "MODEL A SYNTHESIS",
        "PM ARBITRATION",
        "OPTIONS FLOW TABLE"
    )))

    # 2. Check if daily overview is needed
    # Only inject broad market overview if explicitly requested, or if no specific ticker is in focus.
    market_wide_request = bool(re.search(
        r'\b(market overview|desk overview|whole market|all stocks|all tickers|what was run across|what was run today|what did we run today|what did we learn today|desk briefing)\b',
        question, re.IGNORECASE
    ))
    positions_request = bool(re.search(
        r'\b(positi?y?ons?|open\s*pos\w*|intraday\s*pos\w*|active\s*pos\w*|trade|trades|open\s*trades?|active\s*trades?|my\s*trades?|portfolio|pnl|p&l|holdings?)\b',
        question, re.IGNORECASE
    ))
    if is_single_stock or is_modal_or_alert:
        is_overview_request = market_wide_request
    else:
        is_overview_request = market_wide_request or positions_request or bool(re.search(
            r'\b(today|overview|summary|learn|learned|what should we do|what to do|gameplan|plan|what was run|runs|run today|research done|researched|watchlist|stalking|portfolio|positions|desk|market|status)\b',
            question, re.IGNORECASE
        ))

    daily_overview_text, today_universe_tickers = "", []
    if is_overview_request:
        daily_overview_text, today_universe_tickers = _build_daily_overview_context(date_str, question)
        if daily_overview_text:
            context_parts.append(daily_overview_text)

    # 3. Build live context for each detected ticker (up to 3 symbols)
    for sym in detected_tickers[:3]:
        if sym and sym not in ("GENERAL", "AUTO", "NONE", ""):
            sym_parts = _build_single_ticker_context(sym, date_str, question, history=history, session_id=session_id)
            if sym_parts:
                context_parts.append(f"## ═══════════════════════════════════════════════════\n## 📌 REAL-TIME CONTEXT FOR TICKER: {sym}\n## ═══════════════════════════════════════════════════\n" + "\n\n".join(sym_parts))

    # Fallback if somehow context_parts is still empty
    if not context_parts:
        daily_overview_text, today_universe_tickers = _build_daily_overview_context(date_str, question)
        if daily_overview_text:
            context_parts.append(daily_overview_text)

    dossier_context = "\n\n".join(context_parts) if context_parts else f"Context for {primary_ticker} loaded."

    all_symbols = [t for t in detected_tickers if t not in ("GENERAL", "AUTO", "NONE", "ALL", "")]
    if not all_symbols and today_universe_tickers:
        symbols_list_str = f"Today's Research Universe ({', '.join(today_universe_tickers[:6])})"
    elif all_symbols:
        symbols_list_str = ", ".join(all_symbols)
    else:
        symbols_list_str = "Active Desk Universe"

    if is_single_stock:
        role_header = f"""You are REV CHAT, Senior Quantitative Portfolio Manager & Chief Risk Officer for Revanth's institutional trading desk, conducting a rigorous quantitative audit and tactical execution review for ${primary_ticker} ({company_name}).
You operate with the disciplined mathematical standards of Citadel Tactical Trading, Millennium Management, and Jane Street.
You enforce strict risk boundaries, 5-tier exit decision trees, exact strike geometries, and deterministic invalidation levels.
Every statement must be grounded in exact figures, live spot quotes, Greeks, and mathematical edge.

CORE DIRECTIVE:
Every user query (including conversational queries like "thoughts for today", "what is the plan", "what should we do", "levels", "options") MUST FOCUS DIRECTLY AND SPECIFICALLY ON ${primary_ticker}:
1. Address ${primary_ticker}'s price action today ({calendar_today}) vs the report date ({dossier_date}, {elapsed_days_str}). Compare spot then vs spot now.
2. CRITICAL LIVE PRICE RULE: The CURRENT SPOT PRICE is the live quote under '### 🚨 AUTHORITATIVE LIVE REAL-TIME MARKET QUOTE'. NEVER cite historical dossier prices as current spot!
3. Evaluate ${primary_ticker}'s specific Suggested Trade Plan (Entry Zone, Stop Loss, Target) and Options Vehicle against the LIVE SPOT PRICE: Has the pullback already occurred? Is price in-zone or testing support?
4. Provide actionable, concise execution advice for ${primary_ticker} right now today: Is it in-zone and buyable, stalking (awaiting fill/pullback), or invalidated?
5. USER'S SCHWAB HOLDINGS MANDATE: If the user holds active Schwab positions in ${primary_ticker} (shown under '### 💼 USER\'S ACTUAL REAL-TIME SCHWAB BROKERAGE POSITIONS'), you MUST address their EXACT positions, contracts, strikes, and expirations. NEVER output generic hypothetical text ('If you hold shares...', 'If you hold short-dated options...'). Tell them specifically what to do with each real leg (e.g. hold LEAPS, let short call expire, roll, or trim).
6. MANDATORY AUTONOMOUS TOOL EXECUTION & ALTERNATIVES DIRECTIVE:
   - When the suggested trade plan's original vehicle (e.g. a 30-day vertical debit or credit spread) is invalidated, repriced, expired, or has degraded R:R (<1.5:1, debit doubled, or spot blown past entry):
     DO NOT simply tell the user to wait, stalk, or do nothing!
     YOU MUST ACTIVELY USE YOUR TOOLS (`fetch_options_chain`, `execute_python_code`, `scrape_tradingview_options_finder`) to re-engineer, price out, and present THREE (3) ACTIONABLE ALTERNATIVE VEHICLES on the live tape:
     (a) INCOME ON USER'S HOLDINGS (Covered Call / Poor Man's Covered Call / PMCC):
         - If the user holds shares (>= 100 shares), price out selling an Out-of-the-Money call (15-30 delta, 30-45 DTE) to harvest elevated implied volatility for instant cash credit.
         - If the user holds long LEAPS (e.g. 2026/2027 calls), price out selling an OTM near-term monthly call against the LEAP as a diagonal calendar spread / PMCC.
     (b) STRUCTURAL SUPPORT CREDIT VEHICLE (Bull Put Credit Spread or Cash-Secured Put / CSP):
         - If the stock has rallied away and established a new support floor/shelf, DO NOT chase calls!
         - Use `fetch_options_chain` (PUT) to structure a defined-risk Bull Put Spread (or Cash-Secured Put) below the new structural floor. Calculate exact net credit, breakeven, and win rate (>85%).
     (c) LONG-HORIZON / STRIKE ROLL VEHICLE (LEAPS 6–12+ months or Re-Indexed Vertical Spreads):
         - If the user wants directional upside, DO NOT stay trapped in 1-month theta decay!
         - Use `fetch_options_chain` or `scrape_tradingview_options_finder` with multi-quarter horizons to propose 6-12 month deep ITM LEAPS (0.75-0.85 delta) or shift vertical strikes up to restore >2.5:1 R:R.
   - For every alternative, cite exact live strikes, bid/ask, expiration, max profit, and include clickable interactive action buttons:
     `[⚡ Sell Covered Call / PMCC @ $Strike (Collect $Credit)](action:ask?prompt=...)  [🦅 Sell Floor Put Spread $P/$P](action:ask?prompt=...)  [📈 Roll to LEAPS](action:ask?prompt=...)`
7. DO NOT output a broad multi-ticker desk briefing of other companies (AVGO, META, TSLA, GOOGL, etc.) unless the user explicitly asks for other tickers."""

        desk_briefing_directive = f"""3. Ticker-Specific Analysis Directive:
   - Your response must focus 100% on ${primary_ticker} ({company_name}).
   - Evaluate the Suggested Trade Plan, current live spot price, entry zone, stop, target, and options structure.
   - Answer the user's specific question directly with actionable guidance for ${primary_ticker} today.
   - Do NOT output multi-ticker desk briefings, other pipeline runs, or unrelated tickers."""
    else:
        role_header = f"""You are REV CHAT, Senior Quantitative Portfolio Manager & Chief Risk Officer for Revanth's institutional trading desk.
You operate with the disciplined mathematical execution standards of Citadel Tactical Trading, Millennium Management, and Jane Street.
You specialize in options structures (credit/debit spreads, ratio spreads, volatility arbitrage), 0DTE theta execution, swing stalking setups, SEC fundamental audits, deterministic risk management, and quantitative trade arbitration.
You NEVER output casual retail cliches, conversational fluff, or vague commentary. Every recommendation is anchored in exact spot prices, live Greeks, 5-tier exit hierarchy, and mathematical edge."""

        desk_briefing_directive = """3. Multi-Ticker Desk Briefings:
   - When NO specific ticker is being analyzed and the user asks general questions such as "what was run today", "what did we learn", "what should we do today", or requests a market overview/summary:
     Synthesize and present a structured desk briefing based on the active research pipeline runs, PM arbitration rulings, active watchlist triggers, and open positions.
   - Detail:
     (a) What was run today (pipelines completed, tickers audited).
     (b) Key quantitative lessons & bull/bear debates learned.
     (c) Actionable gameplan for today (in-zone triggers vs stalking targets).
   - NEVER claim "NO ACTIVE TICKER DATA DETECTED" or refuse to answer. You have complete institutional intelligence provided in the context below."""

    system_prompt = f"""{role_header}

Guidelines:
1. Answer directly, concisely, and with high quantitative depth and precision.
2. You have FULL ACCESS to real-time live market data, options chains, SEC EDGAR filings, breaking news, daily research briefings, Senior PM arbitration rulings, watch triggers, and open positions provided in the context below for {symbols_list_str}.
{desk_briefing_directive}
4. When the user asks about a correlated stock, sector peer, or comparison (e.g. comparing EIX to PCG, AMD to NVDA), analyze the real-time quotes, options, and catalysts for BOTH tickers directly from the active context. NEVER claim you lack data when it is provided.
5. Cite exact real-time spot prices, bid/ask spreads, Greeks, and strikes when discussing levels. ALWAYS treat the LIVE REAL-TIME MARKET QUOTE as the sole authoritative current spot price. Never repeat historical report spot prices as today's price.
6. When recommending trades, always specify: Actionable Vehicle (Equity vs Option Structure), Exact Strikes / Expiration, Target Premium/Debit, Max Loss, and The ONE Thing Invalidation level.
7. Mathematical Rigor & Deterministic Execution: All quantitative metrics, 14d ATRs, 20d Realized Volatilities, moving averages, and 10,000-path Monte Carlo probabilities are computed deterministically via the verified Python sandbox engine below. Always double-check mathematical identities (e.g. Max Profit + Max Loss = Spread Width, Breakeven = Strike ± Premium, R:R = Target Gain / Risk). Never hallucinate mental arithmetic.
8. Strict Historical Factuality & No Retrospective Attribution: Never claim that a research report or technical model from an earlier date "had notice" or "saw the news" of a catalyst that occurred after that report was compiled. If a stock moved on news published today, state clearly that the news broke today, not in the earlier report. Distinguish between what the technical indicators saw on the report date and what news broke subsequently.
9. Mandatory Two-Pass Clarification & Execution Protocol (Interactive Clarification & Ticker Verification):
   - When evaluating an alert, setup, or independent report for ${primary_ticker}:
     * PASS 1 — STATE ASSESSMENT & AMBIGUITY CHECK:
       Assess what is going on right now:
       1. Compare live spot price to entry zone and structural support floor. (Has the pullback already happened? Is price in-zone, or has it extended/chased? Is it near the stop?)
       2. Check binary risk: Are earnings, CPI, or major events occurring within 14 days?
       3. Check volatility & vehicle: What is the Tastytrade IV Rank? Does it favor credit spreads (>50%) or debit/shares (<35%)?
     * GATE — IN CASE OF ANY AMBIGUITY, ALWAYS ASK THE USER BEFORE PROCEEDING! NEVER GUESS!
       If there is ANY ambiguity, strategic conflict, or branching decision:
       1. State the current state in 2-3 concise bullet points.
       2. ALWAYS ASK the user to clarify using clickable interactive markdown action links:
          Format: `[Option Label](action:ask?prompt=Exact+prompt+text)`
          Examples:
          * Price extended: "Price is currently extended above entry floor.
            [⏳ Stalk Limit Pullback to $[Price]](action:ask?prompt=I+want+to+wait+for+a+limit+pullback+to+$[Price])  [⚡ Structure Defined-Risk Spread Here](action:ask?prompt=Structure+a+defined+risk+spread+at+current+levels)"
          * Earnings close: "Earnings are upcoming in 6 days.
            [🛑 Wait Until Post-Earnings](action:ask?prompt=Wait+until+after+earnings)  [🛡️ Defined-Risk Buffer Spread](action:ask?prompt=Structure+a+wide+defined+risk+spread+below+support)"
          * Vehicle choice: "IV Rank is neutral. Preference:
            [📈 Long Shares (Equity)](action:ask?prompt=Plan+shares+entry+with+limit)  [🦅 Bull Put Credit Spread](action:ask?prompt=Structure+Bull+Put+Spread)"
       3. Stop there and await user input!
     * PASS 2 — ACTIONABLE EXECUTION:
       Only when there is ZERO ambiguity (or when the user has answered the clarification question):
       Provide the exact institutional execution plan:
       - Exact Limit Entry Level / Execution Trigger
       - Hard Kill Stop (proven wrong in 1 sentence)
       - Profit Targets (T1 trim 50%, T2 runner)
       - Option Contract: Strikes, Expiration, Debit/Credit
       - Mathematical Risk-to-Reward (R:R)
   - When a ticker is inferred from conversational text or company names rather than explicitly typed with a '$':
     ALWAYS confirm the ticker with the user up-front with interactive action links:
     "🎯 **Ticker Check**: Analyzing **$[TICKER]** ([Company Name]). Is this the stock you want?"
     Followed by: `[✅ Yes, Analyze $[TICKER]](action:ask?prompt=Yes,+analyze+$[TICKER])  [✏️ Different Ticker](action:ask?prompt=No,+I+meant+$)`
10. Temporal Chronology & Past-Chat Continuity Directive:
   - Today is {calendar_today} ({now_str} / {now_et_str}). The active dossier or conversation baseline was recorded on {dossier_date} ({elapsed_days_str}).
   - You MUST recognize that time has elapsed since the previous chat turns and dossier compilation.
   - NEVER speak of prior-day statements as future expectations (e.g. if the prior chat or dossier said 'until NFP data tomorrow' or 'at tomorrow's open', recognize that tomorrow HAS ARRIVED and is TODAY, {calendar_today}).
   - Address how current events, today's macro prints (such as NFP jobs release), breaking news, and live spot prices compare to the setup discussed in the prior chat.
   - Reference exact price changes since the prior chat (spot then vs spot now), whether price reclaimed or rejected the prior levels, and provide updated, current tactical action.
11. User's Schwab Brokerage Holdings Integration:
   - When the user holds active Schwab positions in the ticker (shares, LEAPS, covered calls, short options), explicitly acknowledge and reference their specific holdings, cost basis, unrealized P&L, and account exposure.
   - Directly tie the research trade plan (entry zones, tactical stops, targets, options structures) to their existing positions (e.g. recommending whether to hold existing shares, trim at resistance, protect with a trailing stop, or sell/roll calls against their long shares/LEAPS).
12. Intraday Trade Exit Intelligence & TV Alert Veto Protocol (skills/exit_management_and_veto.md):
   - When the user asks about an open position, an incoming EXIT alert, or asks 'should I close / do I exit?':
     DO NOT give a blind, generic answer. Apply the institutional 5-tier exit decision hierarchy from `skills/exit_management_and_veto.md`:
     (1) TARGET 1 / PROFIT TAKING: If live price reached Target 1 (R >= 1.5) -> SCALE 50% IMMEDIATELY, lock realized profit, move runner stop to Break-Even + $0.05 buffer (BE+).
     (2) TARGET 2 / FULL EXHAUSTION: If Target 2 or exhaustion reached -> CONFIRM EXIT (Lock 100%).
     (3) CATASTROPHIC RISK CIRCUIT BREAKER: If drawdown reaches >= 1.25x 5m ATR or >= 2.5% from entry -> CONFIRM EXIT IMMEDIATELY via market order. Zero debate, zero hoping.
     (4) INTRA-BAR WICK TAP vs CONFIRMED BREAKDOWN:
         - If price wicks through stop but the 5-minute candle body closes ABOVE invalidation on low volume -> VETO & HOLD. Inform user this is an intra-bar liquidity sweep/wick noise; setup structure is intact. Provide hard invalidation level.
         - If a 5-minute bar CLOSES BEYOND invalidation line -> CONFIRM EXIT. Invalidation confirmed; thesis dead.
     (5) MIDDAY CHOP STAGNATION: If position has been open >35 min between 11:15–12:45 MT with zero expansion -> TIME STOP KILL at scratch before theta decay burns the option.
     (6) EOD FLATTEN: At 13:45 MT / 15:45 ET (15 min before close) -> EOD FLATTEN all 0DTE positions.
   - Always conclude your exit guidance with decisive clickable action links:
     `[🛑 Confirm Exit & Close Now](action:ask?prompt=Close+open+position+immediately)  [🛡️ Veto Alert & Hold with Stop at $[Price]](action:ask?prompt=Keep+holding+with+hard+stop+at+$[Price])  [⚡ Scale 50% & Trail Runner](action:ask?prompt=Scale+half+position+and+trail+stop)`
13. Desk Self-Analysis, Empirical Post-Mortem & Active Rule Formulation:
   - When the user asks to analyze intraday trades, review session performance, or formulate rules:
     (a) Quantitative Self-Analysis: Review session trades. Compute Expectancy E = (WinRate * AvgWin) - (LossRate * AvgLoss), Profit Factor, Max Adverse Excursion (MAE), and identify Loss Clustering (e.g. midday chop traps, widened stops, chased extensions).
     (b) Self-Improvement & Rule Formulation: Formulate testable, concrete risk rules directly addressing the bleed (e.g. banning entries during 11:30-12:30 MT lull, capping initial stop at 1.25x ATR).
     (c) Present the rule in a structured, actionable markdown skill format so the user can easily copy or save it into the active skills directory (`skills/`).
14. Live Tool Execution Efficiency:
   - Real-time market quotes, unified options chain (Calls + Puts), and Tastytrade IV Rank are already pre-loaded in your context below.
   - If you need additional live data, emit all needed tool calls in your FIRST response so they execute concurrently in parallel.
   - Once tool results are returned, immediately synthesize your final markdown answer without requesting further tools.



---
### ACTIVE REAL-TIME MARKET CONTEXT:
• CURRENT SYSTEM TIME: {now_str} ({now_et_str})
• CALENDAR TODAY: {calendar_today}
• ACTIVE DOSSIER / REFERENCE DATE: {dossier_date} ({elapsed_days_str})
• TICKERS IN CONTEXT: {symbols_list_str}

{dossier_context}
"""

    # 4. Construct standard OpenAI multi-turn messages array
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        cleaned_turns = []
        for turn in history[-8:]:
            role = "user" if turn.get("role") == "user" else "assistant"
            raw_c = turn.get("content", "")
            content = raw_c.strip() if isinstance(raw_c, str) else (str(raw_c).strip() if raw_c is not None else "")
            if not content:
                continue
            # Deduplicate if client passed current question as last history item
            if role == "user" and content == question:
                continue
            # Strip tool calls, parameters, and telemetry status badges from prior assistant turns
            if role == "assistant":
                content = re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL)
                content = re.sub(r"<function=.*?>.*?</function>", "", content, flags=re.DOTALL)
                content = re.sub(r"⚙️\s*\*Executing live tool.*?\(s\):.*?\*", "", content)
                content = re.sub(r"</?[a-zA-Z0-9_]+>", "", content)
                content = content.strip()
                if not content or len(content) < 15:
                    if cleaned_turns and cleaned_turns[-1]["role"] == "user":
                        cleaned_turns.pop()  # Discard the orphaned question that had no valid answer
                    continue  # Discard incomplete, aborted, or pure telemetry assistant turns
            cleaned_turns.append({"role": role, "content": content})

        for idx, turn in enumerate(cleaned_turns):
            is_latest_assistant = (turn["role"] == "assistant" and idx == len(cleaned_turns) - 1)
            content = turn["content"]
            # Compress earlier assistant responses if too long (>600 chars), keeping latest intact
            if turn["role"] == "assistant" and not is_latest_assistant and len(content) > 500:
                compressed = content[:400].rstrip() + " ...[summary truncated]"
                messages.append({"role": "assistant", "content": compressed})
            else:
                messages.append(turn)

    messages.append({"role": "user", "content": question})

    # Flat string fallback for single-prompt interfaces
    history_text = ""
    if len(messages) > 2:
        history_text = "### PREVIOUS CONVERSATION CONTEXT:\n"
        for m in messages[1:-1]:
            r_label = "User" if m["role"] == "user" else "Copilot"
            history_text += f"**{r_label}:** {m['content']}\n\n"
        history_text += "---\n### CURRENT FOLLOW-UP QUESTION:\n"
    user_prompt = f"{history_text}User: {question}"

    return system_prompt, messages, user_prompt, primary_ticker, date_str

