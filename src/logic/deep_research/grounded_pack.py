"""grounded_pack.py — six targeted Google-grounded research questions per ticker.

Each question is its own titled, citable SOURCES section. They run in parallel and are
disk-cached under the ticker's raw artifact dir so a re-run spends no grounding quota.
Citations (title + URL) are preserved for the report's numbered Sources list.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_TAIL = ("Dated facts with numbers only; write dates as YYYY-MM-DD; say \"unknown\" where a fact "
         "is not found. No opinions, no recommendations.")

TOPICS: List[Tuple[str, str, str]] = [
    ("results", "Latest results & guidance",
     "Latest quarterly results for {t}: revenue, EPS, gross and operating margins, free cash flow, "
     "segment or product-line growth, guidance for next quarter and the full year versus consensus, "
     "and the earnings report date. " + _TAIL),
    ("valuation", "Valuation & analysts",
     "{t} valuation and analyst view: forward P/E, EV/sales versus direct peers and versus its own "
     "5-year range, consensus price target low/median/high, and every rating or price-target change "
     "in the last 30 days with its date. " + _TAIL),
    ("competition", "Competitive position & industry",
     "{t} competitive position and industry: market share versus named rivals, the secular drivers "
     "of the business, and the main competitive threats with dates. " + _TAIL),
    ("catalysts", "Catalyst calendar",
     "Catalyst calendar for {t} over the next 6 months: the next earnings date, product launches, "
     "regulatory decisions, investor days, share-lockup expirations, index additions or removals, "
     "and the upcoming FOMC, CPI and NFP dates. " + _TAIL),
    ("risks", "Risks",
     "Known risks for {t}: litigation, regulation and investigations, customer concentration, debt "
     "maturities, insider selling, and short interest. " + _TAIL),
    ("sector", "Sector & market context",
     "{t} sector and market context: the sector's recent trend versus the S&P 500, the "
     "interest-rate backdrop, and market sentiment relevant to this stock. " + _TAIL),
    ("exposure", "Policy & macro exposure",
     "Policy and macro exposure for {t}: revenue by geography; subsidies, tax credits and "
     "government contracts; political or regulatory ties of executives and owners; dependence "
     "on AI and datacenter capex; China and tariff exposure; interest-rate sensitivity; and "
     "named key suppliers and customers with their tickers. " + _TAIL),
]


def _cache_path(cache_dir: Path, ticker: str, topic: str) -> Path:
    return Path(cache_dir) / f"{ticker.replace(':', '_')}_grounded_{topic}.json"


def _ask_one(ticker: str, cache_dir: Path, topic: str, title: str, question: str) -> Dict[str, Any]:
    from src.clients.google_grounding_client import ask_grounded

    path = _cache_path(cache_dir, ticker, topic)
    if path.exists():
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(out, dict) and out.get("topic") == topic:
                return out
        except Exception:
            pass
    res: Dict[str, Any] = {}
    try:
        res = ask_grounded(question.format(t=ticker)) or {}
    except Exception as e:
        logger.debug(f"[{ticker}] grounded {topic} failed: {e}")
    out = {"topic": topic, "title": title, "text": str(res.get("text") or ""),
           "citations": [c for c in (res.get("citations") or []) if isinstance(c, dict)
                         and c.get("url")]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"[{ticker}] grounded {topic} cache write failed: {e}")
    return out


def fetch_grounded_pack(ticker: str, cache_dir: Path) -> List[Dict[str, Any]]:
    """Ask the six questions in parallel (TOPICS order preserved). Returns one dict per topic."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(TOPICS)) as ex:
        return list(ex.map(lambda a: _ask_one(ticker, cache_dir, *a), TOPICS))


def pack_sections(pack: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """One (title, body) citable source section per topic; citations appended for audit."""
    sections: List[Tuple[str, str]] = []
    for item in pack:
        body = str(item.get("text") or "").strip() or "(unavailable)"
        cites = item.get("citations") or []
        if cites:
            body += "\n\nSources:\n" + "\n".join(f"  - {c.get('title')}: {c.get('url')}" for c in cites)
        sections.append((f"Grounded: {item.get('title') or item.get('topic')}", body))
    return sections


def pack_block(ticker: str, pack: List[Dict[str, Any]]) -> str:
    """Single text block for the prompt builders that take one grounded string."""
    parts = []
    for title, body in pack_sections(pack):
        parts.append(f"--- GOOGLE-GROUNDED RESEARCH ({ticker}): {title} ---\n{body}\n")
    return "\n".join(parts)


def pack_citations(pack: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flat deduped [{title, url, topic}] for the report's numbered Sources list."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in pack:
        for c in item.get("citations") or []:
            url = str(c.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"title": str(c.get("title") or url), "url": url,
                        "topic": str(item.get("title") or item.get("topic") or "")})
    return out
