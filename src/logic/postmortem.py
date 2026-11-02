"""Big-drop post-mortem: why a name fell, from measured context plus the company's own filing text.

Labels use the study's rules (move_postmortem.py): EARNINGS when the drop day is an earnings reaction in the
ledger, else MARKET (SPY <= -3%), else INDUSTRY (same-industry peers mean <= -3%), else IDIOSYNCRATIC.
The market leg is SPY (not the full universe) and industry is the current profiles snapshot.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src import config

logger = logging.getLogger(__name__)

DROP = -0.10
WIDE = -0.03
MARKET = "SPY"
EXHIBIT_CHARS = 12000
SURPRISE_BAND = 2.0
SURPRISE_MATCH_DAYS = 4
PROFILES = config.BASE_DIR / "data" / "universe" / "profiles.json"
CIK_MAP = config.BASE_DIR / "data" / "sec_cik_map.json"
EPS_CACHE = config.BASE_DIR / "data" / "research" / "eps_surprise.json"
REPORTS = config.BASE_DIR / "reports"


def label_move(ticker_ret: float, spy_ret: Optional[float], peer_mean: Optional[float], on_earnings: bool) -> str:
    if on_earnings:
        return "EARNINGS"
    if spy_ret is not None and spy_ret <= WIDE:
        return "MARKET"
    if peer_mean is not None and peer_mean <= WIDE:
        return "INDUSTRY"
    return "IDIOSYNCRATIC"


def surprise_bucket(pct: float) -> str:
    return "MISS" if pct < -SURPRISE_BAND else "BEAT" if pct > SURPRISE_BAND else "IN_LINE"


def select_8k(submissions: Dict[str, Any], day: date) -> Optional[Dict[str, str]]:
    """Most recent 8-K with Item 2.02 filed on day or the session before (after-close releases)."""
    rec = (submissions.get("filings") or {}).get("recent") or {}
    lo, hi = (day - timedelta(days=1)).isoformat(), day.isoformat()
    hits = []
    for form, fd, items, acc, doc in zip(rec.get("form", []), rec.get("filingDate", []), rec.get("items", []),
                                         rec.get("accessionNumber", []), rec.get("primaryDocument", [])):
        if form == "8-K" and "2.02" in (items or "").split(",") and lo <= fd <= hi:
            hits.append({"accession": acc, "primaryDocument": doc, "filingDate": fd})
    return max(hits, key=lambda h: h["filingDate"]) if hits else None


def find_exhibit_url(index_html: str, base: str) -> Optional[str]:
    m = re.search(r'href="([^"]*ex-?99(?:\.|_|-)?0?1[^"]*)"', index_html, re.I) or re.search(r'href="([^"]*ex-?99[^"]*)"', index_html, re.I)
    if not m:
        return None
    href = m.group(1)
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.sec.gov" + href
    return base.rsplit("/", 1)[0] + "/" + href


def strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def _sec_get(url: str, host_json: bool = False) -> str:
    import requests

    from src.clients.sec_edgar_client import SEC_HEADERS

    headers = dict(SEC_HEADERS) if host_json else {"User-Agent": SEC_HEADERS["User-Agent"]}
    r = requests.get(url, headers=headers, timeout=(3, 15))
    r.raise_for_status()
    return r.text


def fetch_filing_text(ticker: str, day: date) -> Optional[str]:
    cik = json.loads(CIK_MAP.read_text()).get(ticker) if CIK_MAP.is_file() else None
    if not cik:
        return None
    subs = json.loads(_sec_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", host_json=True))
    hit = select_8k(subs, day)
    if not hit:
        return None
    acc_nodash = hit["accession"].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{hit['accession']}-index.htm"
    url = find_exhibit_url(_sec_get(base), base)
    if not url:
        return None
    return strip_html(_sec_get(url))[:EXHIBIT_CHARS]


def _ledger_rows(day: date) -> List[Dict[str, Any]]:
    from src.logic import earnings_move

    rows = []
    if earnings_move.LEDGER.is_file():
        for line in earnings_move.LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _eps_match(ticker: str, day: date, eps: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    best = None
    for q in (eps.get(ticker, {}) or {}).get("quarters") or []:
        try:
            rd = datetime.strptime(q["reportedDate"], "%Y-%m-%d").date()
            pct = float(q["surprisePercentage"])
        except (KeyError, TypeError, ValueError):
            continue
        gap = abs((rd - day).days)
        if gap <= SURPRISE_MATCH_DAYS and (best is None or gap < best["gap"]):
            best = {"reported": rd.isoformat(), "pct": pct, "bucket": surprise_bucket(pct), "gap": gap}
    return best


def _industry_peers(ticker: str, profiles: Dict[str, Any]) -> List[str]:
    ind = (profiles.get(ticker) or [None, {}])[1].get("industry") if profiles.get(ticker) else None
    if not ind:
        return []
    return sorted(t for t, v in profiles.items() if t != ticker and isinstance(v, list) and len(v) > 1 and v[1].get("industry") == ind)


def _load_profiles() -> Dict[str, Any]:
    return json.loads(PROFILES.read_text()) if PROFILES.is_file() else {}


def download_closes(tickers: Iterable[str], period: str = "2y"):
    import pandas as pd
    import yfinance as yf

    tk = sorted(set(tickers))
    px = yf.download(tk, period=period, auto_adjust=True, progress=False, group_by="column")["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(tk[0])
    return px.dropna(how="all")


def build_card(ticker: str, day: date, closes=None, profiles: Optional[Dict[str, Any]] = None,
               ledger: Optional[List[Dict[str, Any]]] = None, eps: Optional[Dict[str, Any]] = None,
               dw: Optional[Dict[str, Any]] = None, llm: bool = True, filing_fetch=None) -> Dict[str, Any]:
    import pandas as pd

    from src.logic import earnings_move

    ticker = ticker.upper()
    profiles = _load_profiles() if profiles is None else profiles
    peers = _industry_peers(ticker, profiles)
    if closes is None:
        closes = download_closes([ticker, MARKET, *peers])
    ledger = _ledger_rows(day) if ledger is None else ledger
    eps = (json.loads(EPS_CACHE.read_text()) if EPS_CACHE.is_file() else {}) if eps is None else eps

    idx = [pd.Timestamp(d).date() for d in closes.index]
    if day not in idx:
        return {"ticker": ticker, "date": day.isoformat(), "error": "no bar on this date"}
    i = idx.index(day)
    ret = closes.pct_change()
    move = float(ret[ticker].iat[i])
    spy = float(ret[MARKET].iat[i]) if MARKET in closes.columns and pd.notna(ret[MARKET].iat[i]) else None
    peer_cols = [t for t in peers if t in closes.columns and pd.notna(ret[t].iat[i])]
    peer_moves = {t: float(ret[t].iat[i]) for t in peer_cols}
    peer_mean = sum(peer_moves.values()) / len(peer_moves) if peer_moves else None
    ranked = sorted(peer_moves.items(), key=lambda kv: kv[1])
    react = [r for r in ledger if r.get("ticker") == ticker and r.get("report_date") in {day.isoformat(), (day - timedelta(days=1)).isoformat()}]
    label = label_move(move, spy, peer_mean, bool(react))
    fc = earnings_move.forecast(ticker) or {}
    run21 = float(closes[ticker].iat[i - 1] / closes[ticker].iat[i - 22] - 1) if i >= 22 else None
    card = {
        "ticker": ticker, "date": day.isoformat(), "label": label, "move_pct": round(move * 100, 2),
        "market_spy_pct": None if spy is None else round(spy * 100, 2),
        "industry_ex_self_pct": None if peer_mean is None else round(peer_mean * 100, 2),
        "peers_worst": [[t, round(v * 100, 2)] for t, v in ranked[:5]],
        "peers_best": [[t, round(v * 100, 2)] for t, v in ranked[-3:]],
        "prior_close": {"run21_pct": None if run21 is None else round(run21 * 100, 2),
                        "ext_z": (dw or {}).get("Ext Z Self Relative"), "iv_rank": (dw or {}).get("Energy IV Rank Pct"),
                        "ext_pct_vs_ma200": (dw or {}).get("Ext Pct vs MA200")},
        "own_history": {"median_abs_pct": round(fc["median_abs"] * 100, 2) if fc else None,
                        "move_vs_median": round(abs(move) / fc["median_abs"], 2) if fc and fc.get("median_abs") else None},
        "ledger": ({k: react[-1].get(k) for k in ("report_date", "implied_move_pct", "own_median_pct", "own_p95_pct")}
                   if react else None),
        "surprise": _eps_match(ticker, day, eps),
    }
    if label in ("EARNINGS", "IDIOSYNCRATIC") and llm:
        card["why"] = explain(ticker, day, label, filing_fetch or fetch_filing_text)
    return card


def explain(ticker: str, day: date, label: str, filing_fetch) -> Dict[str, Any]:
    source, text = None, None
    try:
        text = filing_fetch(ticker, day)
        source = "8-K exhibit 99.1" if text else None
    except Exception as e:
        logger.debug("[%s] filing fetch failed: %s", ticker, e)
    if not text and label == "IDIOSYNCRATIC":
        try:
            from src.clients.finnhub_client import get_company_news

            heads = [a.get("headline", "") for a in get_company_news(ticker, days=2)[:10] if a.get("headline")]
            text = "\n".join(f"- {h}" for h in heads) or None
            source = "Finnhub headlines (2d)" if text else None
        except Exception as e:
            logger.debug("[%s] headlines failed: %s", ticker, e)
    if not text:
        return {"source": None, "bullets": None, "note": "no filing or headline text"}
    from src.clients.llm_client import query_local_llm

    system = ("You explain a large stock drop for a trader. Use only the text given. Output exactly 3 bullets: "
              "(1) results vs guidance or consensus, (2) guidance change: raised, cut, in-line or none, "
              "(3) the stated reason. Say 'not stated' where the text does not say.")
    out = query_local_llm(system_prompt=system, user_prompt=text, max_tokens=600, disable_thinking=True)
    return {"source": source, "bullets": (out or "").strip() or None}


def render_markdown(card: Dict[str, Any]) -> str:
    if card.get("error"):
        return f"# {card['ticker']} post-mortem {card['date']}\n\n{card['error']}\n"
    lines = [
        f"# {card['ticker']} post-mortem {card['date']}: {card['move_pct']:+.1f}% ({card['label']})",
        "",
        f"- Market (SPY): {card['market_spy_pct']}% | industry ex-self: {card['industry_ex_self_pct']}%",
        f"- Worst peers: {', '.join(f'{t} {v:+.1f}%' for t, v in card['peers_worst'])}",
        f"- Prior close: 21-bar run {card['prior_close']['run21_pct']}%, Ext Z {card['prior_close']['ext_z']}, "
        f"IV rank {card['prior_close']['iv_rank']}",
    ]
    oh = card.get("own_history") or {}
    if oh.get("median_abs_pct") is not None:
        lines.append(f"- Own median earnings move {oh['median_abs_pct']}%: this move is {oh['move_vs_median']}x")
    if card.get("ledger"):
        lines.append(f"- Ledger: implied {card['ledger'].get('implied_move_pct')}% for report {card['ledger'].get('report_date')}")
    if card.get("surprise"):
        s = card["surprise"]
        lines.append(f"- EPS surprise {s['pct']:+.1f}% ({s['bucket']}, reported {s['reported']})")
    why = card.get("why") or {}
    if why:
        lines += ["", f"## Why ({why.get('source') or 'no source'})", why.get("bullets") or why.get("note") or ""]
    return "\n".join(lines) + "\n"


def write_card(card: Dict[str, Any]) -> Path:
    d = REPORTS / card["date"]
    d.mkdir(parents=True, exist_ok=True)
    md = d / f"{card['ticker']}_postmortem.md"
    md.write_text(render_markdown(card), encoding="utf-8")
    (d / f"{card['ticker']}_postmortem.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    return md


def notify_card(card: Dict[str, Any], notifier=None) -> bool:
    if notifier is None:
        from src.tracking.notify import notify as notifier
    title = f"POSTMORTEM {card['ticker']} {card['move_pct']:+.1f}% {card['label']}"
    body = f"{card['date']}: {card['label']}; SPY {card['market_spy_pct']}%, industry {card['industry_ex_self_pct']}%"
    return notifier("RISK", title, body, dedupe_key=f"POSTMORTEM:{card['ticker']}:{card['date']}")


def scan(day: Optional[date] = None, notifier=None, closes=None) -> Dict[str, Any]:
    """Nightly: held + watch names + profile universe; cards for drops of -10% or worse, or 2x own median on an earnings day."""
    from src.logic import earnings_move

    profiles = _load_profiles()
    held = _names("schwab_portfolio.db", "select distinct symbol from schwab_positions")
    watch = _names("research_watch.db", "select ticker from watch_targets")
    universe = sorted(set(profiles) | set(held) | set(watch))
    closes = download_closes(universe, period="10d") if closes is None else closes
    last = closes.index[-1]
    day = day or (last.date() if hasattr(last, "date") else last)
    if day.isoformat() not in closes.index.strftime("%Y-%m-%d"):
        return {"date": day.isoformat(), "hits": 0, "note": "no bar on this date"}
    r = closes.pct_change(fill_method=None).loc[closes.index.strftime("%Y-%m-%d") == day.isoformat()].iloc[0]
    ledger = _ledger_rows(day)
    recent_report = {(x["ticker"]) for x in ledger if x.get("report_date") in {day.isoformat(), (day - timedelta(days=1)).isoformat()}}
    hits = []
    for t in closes.columns:
        v = r.get(t)
        if v is None or v != v:
            continue
        med = (earnings_move.forecast(t) or {}).get("median_abs")
        if v <= DROP or (t in recent_report and med and v <= -2 * med):
            hits.append(t)
    out = {"date": day.isoformat(), "hits": len(hits), "cards": []}
    mine = set(held) | set(watch)
    for t in hits:
        try:
            card = build_card(t, day, profiles=profiles, llm=True)
            write_card(card)
        except Exception as e:
            logger.warning("[%s] postmortem card failed: %s", t, e)
            continue
        # Notify only held/watch names: a market-wide selloff would otherwise send one message per universe name.
        if t in mine:
            notify_card(card, notifier)
        out["cards"].append({"ticker": t, "label": card.get("label"), "move_pct": card.get("move_pct")})
    return out


def _names(db: str, sql: str) -> List[str]:
    import sqlite3

    p = config.BASE_DIR / "data" / db
    if not p.is_file():
        return []
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, timeout=5)
        out = [str(r[0]).upper() for r in c.execute(sql) if r[0]]
        c.close()
        return out
    except sqlite3.Error:
        return []


def recent_card_block(ticker: str, today: Optional[date] = None, days: int = 30, max_chars: int = 1500) -> str:
    """Latest post-mortem text for the ticker in the last `days` days, for the judge's prior-plan context."""
    today = today or date.today()
    for back in range(days + 1):
        d = today - timedelta(days=back)
        md = REPORTS / d.isoformat() / f"{ticker.upper()}_postmortem.md"
        if md.is_file():
            return ("RECENT POST-MORTEM (measured context and filing text; not a veto):\n"
                    + md.read_text(encoding="utf-8")[:max_chars])
    return ""
