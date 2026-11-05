"""
SEC EDGAR API Client for Automated Regulatory Filing & Fundamental Audit.
Connects directly to the official U.S. SEC EDGAR REST API (https://data.sec.gov).
Pulls 10-K, 10-Q, 8-K filings, Form 4 insider transactions, and XBRL financial facts.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import ssl
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.error

project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src import config

logger = logging.getLogger(__name__)

# SEC requires a compliant User-Agent: "Name/Version (contact@email.com)"
_SEC_UA = os.getenv("SEC_USER_AGENT", "AntigravityStockResearch/1.0 (research@antigravitystock.com)")

SEC_HEADERS = {
    "User-Agent": _SEC_UA,
    "Accept-Encoding": "identity",  # data.sec.gov returns 403 with gzip request on some CDN nodes
    "Host": "data.sec.gov",
}

SEC_WWW_HEADERS = {
    "User-Agent": _SEC_UA,
    "Accept-Encoding": "gzip, deflate",
    "Host": "www.sec.gov",
}

CIK_MAP_PATH = config.BASE_DIR / "data" / "sec_cik_map.json"
SEC_CACHE_DIR = config.BASE_DIR / "data" / "sec_cache"

# Build a robust SSL context that trusts system + certifi roots.
def _make_ssl_context() -> Optional[ssl.SSLContext]:
    """Returns certifi SSL context if available, else None (urllib default is fine on Windows)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None  # fall back to urllib default SSL handling

_SSL_CTX = _make_ssl_context()
_SEC_TIMEOUT = 12.0  # SEC EDGAR CDN can be slow on first hit; 12s is safe
_SEC_RETRIES = 2


def _fetch_url_raw(url: str, headers: dict) -> Optional[bytes]:
    """Fetch raw bytes from a URL with SEC-compliant headers, SSL context, retry, and gzip decode."""
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(_SEC_RETRIES + 1):
        try:
            open_kwargs: dict = {"timeout": _SEC_TIMEOUT}
            if _SSL_CTX is not None:
                open_kwargs["context"] = _SSL_CTX
            with urllib.request.urlopen(req, **open_kwargs) as resp:
                raw = resp.read()
                enc = (resp.info().get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            logger.warning(f"SEC EDGAR HTTP {e.code} on attempt {attempt + 1} for {url}: {e.reason}")
            if e.code in (403, 404):
                return None  # no point retrying
        except Exception as e:
            logger.warning(f"SEC EDGAR fetch attempt {attempt + 1} failed for {url}: {e}")
            # On timeout with context, retry without context in case of proxy/SSL routing issue
            if attempt == 0 and _SSL_CTX is not None and "timed out" in str(e).lower():
                try:
                    with urllib.request.urlopen(req, timeout=_SEC_TIMEOUT) as resp:
                        raw = resp.read()
                        enc = (resp.info().get("Content-Encoding") or "").lower()
                        if "gzip" in enc:
                            raw = gzip.decompress(raw)
                        return raw
                except Exception as e2:
                    logger.warning(f"SEC EDGAR fetch attempt {attempt + 1} (no-ctx fallback) failed for {url}: {e2}")
    return None


def _fetch_url_json(url: str, headers: dict) -> Optional[dict]:
    """Helper to fetch and decode JSON from SEC.gov with proper headers and error handling."""
    raw = _fetch_url_raw(url, headers)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception as e:
        logger.warning(f"SEC EDGAR JSON decode failed for {url}: {e}")
        return None


def get_cik_for_ticker(ticker: str) -> Optional[str]:
    """Resolves a stock ticker to its official 10-digit zero-padded SEC CIK string."""
    ticker_clean = ticker.strip().upper()

    # 1. Check All_tickrs/company_tickers.json (Primary offline dataset)
    all_tickrs_path = config.BASE_DIR / "All_tickrs" / "company_tickers.json"
    if all_tickrs_path.exists():
        try:
            raw_all = json.loads(all_tickrs_path.read_text(encoding="utf-8"))
            for item in raw_all.values() if isinstance(raw_all, dict) else raw_all:
                if str(item.get("ticker", "")).strip().upper() == ticker_clean:
                    c = item.get("cik_str")
                    if c:
                        return str(c).zfill(10)
        except Exception:
            pass

    # 2. Check local cached map (data/sec_cik_map.json)
    if CIK_MAP_PATH.exists():
        try:
            cik_map = json.loads(CIK_MAP_PATH.read_text(encoding="utf-8"))
            if ticker_clean in cik_map:
                return str(cik_map[ticker_clean]).zfill(10)
        except Exception:
            pass

    # 3. Fallback: Fetch fresh company tickers mapping from SEC
    url = "https://www.sec.gov/files/company_tickers.json"
    data = _fetch_url_json(url, headers=SEC_WWW_HEADERS)
    if data:
        fresh_map = {}
        for item in data.values():
            t = str(item.get("ticker", "")).upper()
            c = str(item.get("cik_str", ""))
            if t and c:
                fresh_map[t] = c

        try:
            CIK_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
            CIK_MAP_PATH.write_text(json.dumps(fresh_map, indent=2), encoding="utf-8")
        except Exception:
            pass

        if ticker_clean in fresh_map:
            return str(fresh_map[ticker_clean]).zfill(10)

    return None


def fetch_sec_filings_data(ticker: str, form_type: str = "ALL", limit: int = 5) -> Dict[str, Any]:
    """
    Pulls recent SEC submissions and XBRL financial facts for a given ticker.
    """
    ticker_clean = ticker.strip().upper()
    cik = get_cik_for_ticker(ticker_clean)
    if not cik:
        return {"error": f"Could not resolve SEC CIK for ticker {ticker_clean}."}

    # 1. Fetch submissions (recent filings)
    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    sub_data = _fetch_url_json(sub_url, headers=SEC_HEADERS)
    if not sub_data:
        return {"error": f"Failed to fetch SEC submissions for CIK {cik} ({ticker_clean})."}

    company_name = sub_data.get("name", ticker_clean)
    sic_desc = sub_data.get("sicDescription", "N/A")
    fiscal_year_end = sub_data.get("fiscalYearEnd", "N/A")

    recent_filings = []
    filings_dict = sub_data.get("filings", {}).get("recent", {})
    forms = filings_dict.get("form", [])
    filing_dates = filings_dict.get("filingDate", [])
    report_dates = filings_dict.get("reportDate", [])
    accession_numbers = filings_dict.get("accessionNumber", [])
    primary_docs = filings_dict.get("primaryDocument", [])
    descriptions = filings_dict.get("primaryDocDescription", [])

    target_forms = ["10-K", "10-Q", "8-K", "4"] if form_type.upper() == "ALL" else [form_type.upper()]

    for i in range(len(forms)):
        f_type = forms[i]
        if any(f_type == tf or f_type.startswith(tf) for tf in target_forms):
            acc_num = accession_numbers[i].replace("-", "")
            prim_doc = primary_docs[i]
            cik_int = str(int(cik))
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_num}/{prim_doc}"

            recent_filings.append({
                "form": f_type,
                "filing_date": filing_dates[i] if i < len(filing_dates) else "N/A",
                "report_date": report_dates[i] if i < len(report_dates) else "N/A",
                "description": descriptions[i] if i < len(descriptions) else f_type,
                "url": doc_url,
            })
            if len(recent_filings) >= limit:
                break

    # 2. Fetch XBRL financial facts
    facts_data = _fetch_facts_json(cik)
    quarterly_financials = []
    shares_outstanding = None

    if facts_data and "facts" in facts_data:
        quarterly_financials, shares_outstanding = _xbrl_quarters(facts_data["facts"])

    return {
        "ticker": ticker_clean,
        "company_name": company_name,
        "cik": cik,
        "sic": sic_desc,
        "fiscal_year_end": fiscal_year_end,
        "recent_filings": recent_filings,
        "quarterly_financials": quarterly_financials,
        "shares_outstanding": shares_outstanding,
    }


def _fetch_facts_json(cik: str) -> Optional[dict]:
    """companyfacts JSON, disk-cached per (cik, calendar day) — the blob is multi-MB."""
    cache = SEC_CACHE_DIR / f"facts_{cik}_{date.today().isoformat()}.json"
    try:
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        pass
    data = _fetch_url_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=SEC_HEADERS)
    if data:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass
    return data


def _duration_days(u: dict) -> int:
    try:
        return (date.fromisoformat(str(u["end"])) - date.fromisoformat(str(u["start"]))).days
    except (KeyError, ValueError, TypeError):
        return 0


def _xbrl_quarters(facts: dict) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Per-quarter GAAP facts. Duration metrics are filtered to single-quarter (~75-105 day)
    periods so a 10-K's annual figure never lands in a quarterly row; instants carry no
    start date and keep the latest filing per period end."""
    us_gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})

    def _units(names: list, unit: str = "USD", src: Optional[dict] = None) -> list:
        """The listed concept with the most recent period: filers switch tags over the years (AMZN's
        `Revenues` stops in 2017), so the first concept present can be years stale."""
        src = us_gaap if src is None else src
        found = [src[n]["units"][unit] for n in names if n in src and unit in (src[n].get("units") or {})]
        return max(found, key=lambda us: max((str(u.get("end") or "") for u in us), default=""), default=[])

    def _filed(u: dict) -> str:
        return str(u.get("filed") or "")

    def _quarterly(units: list, derive: bool = True) -> Dict[str, dict]:
        """10-Q cash-flow facts are year-to-date and 10-Ks carry the year only: when a quarter has no
        3-month fact, an additive metric is YTD(end) minus YTD(previous quarter end) of the same fiscal year."""
        rows: Dict[str, dict] = {}
        ytd: Dict[Tuple[str, str], dict] = {}
        for u in sorted(units, key=_filed, reverse=True):
            if u.get("form") not in ("10-Q", "10-K") or u.get("val") is None:
                continue
            d, key = _duration_days(u), (str(u.get("start") or ""), str(u.get("end") or ""))
            if 75 <= d <= 105:
                rows.setdefault(key[1], u)
                ytd.setdefault(key, u)
            elif derive and 105 < d <= 380:
                ytd.setdefault(key, u)
        if derive:
            for (start, end), u in ytd.items():
                if end in rows:
                    continue
                prior = [(e, v) for (s, e), v in ytd.items() if s == start and e < end]
                if not prior:
                    continue
                p_end, p = max(prior, key=lambda x: x[0])
                try:
                    gap = (date.fromisoformat(end) - date.fromisoformat(p_end)).days
                except ValueError:
                    continue
                if 75 <= gap <= 105:
                    rows[end] = {**u, "val": u["val"] - p["val"]}
        return rows

    def _instants(units: list) -> Dict[str, dict]:
        rows: Dict[str, dict] = {}
        for u in sorted(units, key=lambda x: (str(x.get("end") or ""), _filed(x)), reverse=True):
            if u.get("form") in ("10-Q", "10-K") and u.get("val") is not None and not u.get("start"):
                rows.setdefault(str(u.get("end") or ""), u)
        return rows

    duration_metrics = {
        "revenue": _units(["Revenues", "SalesRevenueNet",
                           "RevenueFromContractWithCustomerExcludingAssessedTax",
                           "SalesRevenueGoodsNet", "OperatingRevenue"]),
        "net_income": _units(["NetIncomeLoss", "ProfitLoss"]),
        "operating_income": _units(["OperatingIncomeLoss"]),
        "ocf": _units(["NetCashProvidedByUsedInOperatingActivities",
                       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
        "capex": _units(["PaymentsToAcquirePropertyPlantAndEquipment",
                         "PaymentsToAcquireProductiveAssets",
                         "PaymentsToAcquireOtherPropertyPlantAndEquipment"]),
    }
    eps_units = _units(["EarningsPerShareDiluted"], unit="USD/shares")
    instant_metrics = {
        "cash": _units(["CashAndCashEquivalentsAtCarryingValue",
                        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
        "long_term_debt": _units(["LongTermDebtNoncurrent", "LongTermDebt",
                                  "LongTermDebtAndCapitalLeaseObligations"]),
    }
    share_units = (_units(["CommonStockSharesOutstanding", "CommonStockSharesIssued"], unit="shares")
                   or _units(["EntityCommonStockSharesOutstanding"], unit="shares", src=dei))

    quarters: Dict[str, dict] = {}
    for metric, units in duration_metrics.items():
        for end, u in _quarterly(units).items():
            q = quarters.setdefault(end, {"end_date": end, "form": u.get("form", ""),
                                          "fy": u.get("fy", ""), "fp": u.get("fp", "")})
            q.setdefault(metric, u.get("val"))
    for end, u in _quarterly(eps_units, derive=False).items():
        q = quarters.setdefault(end, {"end_date": end, "form": u.get("form", ""),
                                      "fy": u.get("fy", ""), "fp": u.get("fp", "")})
        q.setdefault("eps_diluted", u.get("val"))
    for metric, units in instant_metrics.items():
        for end, u in _instants(units).items():
            if end not in quarters:
                continue
            quarters[end].setdefault(metric, u.get("val"))

    shares_outstanding = None
    inst = _instants(share_units)
    if inst:
        shares_outstanding = inst[sorted(inst, reverse=True)[0]].get("val")

    quarterly_financials = sorted(quarters.values(), key=lambda x: str(x.get("end_date", "")), reverse=True)[:4]
    return quarterly_financials, shares_outstanding


def _fmt_curr(val) -> str:
    if val is None:
        return "N/A"
    abs_v = abs(val)
    sign = "-" if val < 0 else ""
    if abs_v >= 1e9:
        return f"{sign}${abs_v / 1e9:.2f}B"
    elif abs_v >= 1e6:
        return f"{sign}${abs_v / 1e6:.1f}M"
    return f"{sign}${val:,.0f}"


def format_sec_report(ticker: str, form_type: str = "ALL", limit: int = 5) -> str:
    """Formats SEC EDGAR data into a compact Markdown audit for the LLM."""
    data = fetch_sec_filings_data(ticker, form_type=form_type, limit=limit)
    if "error" in data:
        return f"SEC EDGAR Audit for {ticker}: {data['error']}"

    lines = [
        f"### 🏛️ SEC EDGAR REGULATORY & FINANCIAL AUDIT: {data['ticker']} ({data['company_name']})",
        f"- **CIK:** `{data['cik']}` | **SIC Industry:** {data['sic']} | **Fiscal Year End:** {data['fiscal_year_end']}",
        "",
        "#### 📋 Recent SEC Filings (Official Records):",
    ]

    if not data["recent_filings"]:
        lines.append("- No recent filings matching criteria found.")
    else:
        for f in data["recent_filings"]:
            lines.append(f"- **[{f['form']}]** (Filed: {f['filing_date']}) · {f['description']} · [View Filing]({f['url']})")

    lines.append("")
    lines.append("#### 💵 Official GAAP Financial Facts (Trailing 4 Quarters):")

    if not data["quarterly_financials"]:
        lines.append("- No XBRL quarterly financial facts available.")
    else:
        lines.append("| Period End | Revenue | Op Income | Net Income | EPS Dil | OCF | Capex | Cash | LTD |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for q in data["quarterly_financials"]:
            lines.append(
                f"| {q.get('end_date', 'N/A')} | {_fmt_curr(q.get('revenue'))} | "
                f"{_fmt_curr(q.get('operating_income'))} | {_fmt_curr(q.get('net_income'))} | "
                f"{_fmt_eps(q.get('eps_diluted'))} | {_fmt_curr(q.get('ocf'))} | {_fmt_curr(q.get('capex'))} | "
                f"{_fmt_curr(q.get('cash'))} | {_fmt_curr(q.get('long_term_debt'))} |"
            )
    if data.get("shares_outstanding"):
        lines.append(f"\nShares outstanding: {int(data['shares_outstanding']):,}")

    return "\n".join(lines)


def _fmt_eps(val) -> str:
    return "N/A" if val is None else f"${float(val):.2f}"


def sec_xbrl_section(ticker: str) -> Optional[Tuple[str, str]]:
    """Citable SOURCES section: single-quarter GAAP facts straight from EDGAR companyfacts."""
    data = fetch_sec_filings_data(ticker)
    if data.get("error") or not data.get("quarterly_financials"):
        return None
    rows = ["| period end | revenue | op income | net income | EPS dil | OCF | capex | cash | LTD |",
            "|---|---|---|---|---|---|---|---|---|"]
    for q in data["quarterly_financials"]:
        rows.append(
            f"| {q.get('end_date', 'N/A')} | {_fmt_curr(q.get('revenue'))} | "
            f"{_fmt_curr(q.get('operating_income'))} | {_fmt_curr(q.get('net_income'))} | "
            f"{_fmt_eps(q.get('eps_diluted'))} | {_fmt_curr(q.get('ocf'))} | {_fmt_curr(q.get('capex'))} | "
            f"{_fmt_curr(q.get('cash'))} | {_fmt_curr(q.get('long_term_debt'))} |")
    body = (f"{data['company_name']} (CIK {data['cik']}) — official GAAP figures (SEC XBRL companyfacts); "
            "one fiscal quarter per row.\n" + "\n".join(rows))
    if data.get("shares_outstanding"):
        body += f"\nShares outstanding: {int(data['shares_outstanding']):,}"
    return "SEC XBRL financials", body


def broker_fundamentals_section(ticker: str, quote_fund: Optional[dict] = None,
                                tt_metrics: Optional[list] = None) -> Optional[Tuple[str, str]]:
    """Citable SOURCES section: P/E, EPS TTM, market cap from the authenticated Schwab quote and
    beta, IV rank, next earnings date from Tastytrade market metrics."""
    lines: List[str] = []
    f = quote_fund or {}
    try:
        from src.clients.schwab_client import get_quote_fundamentals

        f = f or get_quote_fundamentals(ticker) or {}
    except Exception as e:
        logger.debug(f"Schwab fundamentals for {ticker}: {e}")
    pe, eps, mc, dy = f.get("pe_ratio"), f.get("eps_ttm"), f.get("market_cap"), f.get("div_yield")
    row = []
    if pe is not None:
        row.append(f"P/E {float(pe):.2f}")
    if eps is not None:
        row.append(f"EPS TTM ${float(eps):.2f}")
    if mc is not None:
        row.append(f"market cap {_fmt_curr(mc)}")
    if dy:
        row.append(f"div yield {float(dy):.2f}%")
    if row:
        lines.append("Schwab quote fundamentals: " + " · ".join(row))
    items = tt_metrics
    if items is None:
        try:
            from src.clients.tastytrade_client import get_tastytrade_client

            items = get_tastytrade_client().get_market_metrics(ticker)
        except Exception as e:
            logger.debug(f"Tastytrade metrics for {ticker}: {e}")
            items = []
    for m in items or []:
        if str(m.get("symbol", "")).upper() not in ("", ticker.upper().replace("/", ".")):
            continue
        parts = []
        if m.get("beta") is not None:
            parts.append(f"beta {float(m['beta']):.2f}")
        ivr = m.get("tos-implied-volatility-index-rank") or m.get("implied-volatility-index-rank")
        if ivr is not None:
            ivr_f = float(ivr)
            parts.append(f"IV rank {ivr_f * 100:.1f}%" if ivr_f <= 1.0 else f"IV rank {ivr_f:.1f}%")
        ed = (m.get("earnings") or {}).get("expected-report-date")
        if ed:
            parts.append(f"next earnings (Tastytrade) {str(ed)[:10]}")
        if parts:
            lines.append("Tastytrade market metrics: " + " · ".join(parts))
        break
    return ("Broker fundamentals (Schwab, Tastytrade)", "\n".join(lines)) if lines else None


def _fetch_earnings_release_doc(ticker: str) -> Optional[Dict[str, Any]]:
    """The most recent 8-K's Exhibit 99.1 press release: accession, filing date, urls and the
    cleaned text. Cached per filing accession under data/sec_cache so each filing is fetched once."""
    import html as html_mod
    import re

    ticker_clean = ticker.strip().upper()
    cik = get_cik_for_ticker(ticker_clean)
    if not cik:
        return None

    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    sub_data = _fetch_url_json(sub_url, headers=SEC_HEADERS)
    if not sub_data:
        return None

    filings_dict = sub_data.get("filings", {}).get("recent", {})
    forms = filings_dict.get("form", [])
    filing_dates = filings_dict.get("filingDate", [])
    accession_numbers = filings_dict.get("accessionNumber", [])
    items_list = filings_dict.get("items", [])
    primary_docs = filings_dict.get("primaryDocument", [])

    # Locate the most recent 8-K with Item 2.02 (Results of Operations)
    target_acc = None
    target_date = None
    target_primary_doc = None
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        items_str = items_list[i] if i < len(items_list) else ""
        if "2.02" in str(items_str):
            target_acc = accession_numbers[i]
            target_date = filing_dates[i] if i < len(filing_dates) else "N/A"
            target_primary_doc = primary_docs[i] if i < len(primary_docs) else None
            break

    if not target_acc:
        # Fall back to most recent 8-K regardless of item
        for i, form in enumerate(forms):
            if form == "8-K":
                target_acc = accession_numbers[i]
                target_date = filing_dates[i] if i < len(filing_dates) else "N/A"
                target_primary_doc = primary_docs[i] if i < len(primary_docs) else None
                break

    if not target_acc:
        return None

    cache = SEC_CACHE_DIR / f"ex99_{ticker_clean}_{target_acc}.json"
    try:
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        pass

    cik_int = str(int(cik))
    acc_clean = target_acc.replace("-", "")
    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/"
    www_headers = dict(SEC_WWW_HEADERS)

    # Fetch the EDGAR filing-summary.json (the programmatic document index)
    # Format: /Archives/edgar/data/<cik>/<acc_clean>/filing-summary.json
    filing_summary_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/filing-summary.json"
    filing_summary_raw = _fetch_url_raw(filing_summary_url, headers=www_headers)

    exhibit_url = None
    primary_url = None

    if filing_summary_raw:
        try:
            fs = json.loads(filing_summary_raw.decode("utf-8", errors="ignore"))
            for report in (fs.get("FilingSummary", {}).get("MyReports", {}).get("Report", []) or []):
                rtype = str(report.get("ReportType", "")).upper()
                short_name = str(report.get("ShortName", "")).upper()
                fname = report.get("HtmlFileName") or report.get("XmlFileName") or ""
                if "EXHIBIT 99" in short_name or "EX-99" in rtype or "PRESS RELEASE" in short_name:
                    exhibit_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{fname}"
                    break
        except Exception:
            pass

    # Fallback: parse the plain-text index page to find EX-99.1 link
    if not exhibit_url:
        idx_txt_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{target_acc}-index.htm"
        idx_raw = _fetch_url_raw(idx_txt_url, headers=www_headers)
        if idx_raw:
            try:
                idx_html = idx_raw.decode("utf-8", errors="ignore")
                # Look for EX-99 document links
                ex99_matches = re.findall(
                    r'href="(/Archives/edgar/data/' + cik_int + r'/' + acc_clean + r'/[^"]+)"[^>]*>[^<]*(?:EX-99|ex-99|exhibit\s*99|99\.1)',
                    idx_html, re.IGNORECASE
                )
                if not ex99_matches:
                    # Broader scan: any .htm/.html file in a table row next to "EX-99"
                    ex99_matches = re.findall(
                        r'EX-99[^<]*</td>\s*<td[^>]*><a href="(/Archives/edgar/data/' + cik_int + r'/' + acc_clean + r'/[^"]+\.htm[^"]*)"',
                        idx_html, re.IGNORECASE
                    )
                if ex99_matches:
                    exhibit_url = "https://www.sec.gov" + ex99_matches[0]
            except Exception:
                pass

    # Build primary document URL from the submissions listing
    if target_primary_doc:
        primary_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{target_primary_doc}"

    # Fetch and clean the exhibit HTML text
    exhibit_text = ""
    if exhibit_url:
        raw = _fetch_url_raw(exhibit_url, headers=www_headers)
        if raw:
            try:
                text = raw.decode("utf-8", errors="ignore")
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                text = html_mod.unescape(text)
                exhibit_text = text[:4500]
            except Exception:
                exhibit_text = raw[:4500].decode("utf-8", errors="ignore")

    doc = {"ticker": ticker_clean, "cik": cik, "accession": target_acc, "filing_date": target_date,
           "filing_url": filing_url, "primary_url": primary_url, "exhibit_url": exhibit_url,
           "exhibit_text": exhibit_text}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(doc), encoding="utf-8")
    except Exception:
        pass
    return doc


def sec_8k_section(ticker: str) -> Optional[Tuple[str, str]]:
    """Citable SOURCES section: management's own words from the latest 8-K Exhibit 99.1."""
    doc = _fetch_earnings_release_doc(ticker)
    if not doc or not doc.get("exhibit_text"):
        return None
    return ("SEC 8-K earnings release",
            f"8-K filed {doc['filing_date']} (accession {doc['accession']}) — Exhibit 99.1 text:\n"
            + doc["exhibit_text"])


def fetch_earnings_press_release(ticker: str) -> str:
    """
    Fetches the official earnings press release (Exhibit 99.1) from the most recent
    8-K filing with Item 2.02 (Results of Operations) on SEC EDGAR.

    Returns a formatted markdown block with:
    - Filing accession number, filing date, and primary document URL
    - Exhibit 99.1 (press release) URL
    - Raw text excerpt (first 4000 chars) of the press release HTML
    """
    ticker_clean = ticker.strip().upper()
    doc = _fetch_earnings_release_doc(ticker_clean)
    if not doc:
        cik = get_cik_for_ticker(ticker_clean)
        return (f"❌ Could not resolve SEC CIK for {ticker_clean}." if not cik else
                f"❌ No recent 8-K filing found on EDGAR for {ticker_clean}.")

    lines = [
        f"### 📄 OFFICIAL EARNINGS PRESS RELEASE — {ticker_clean} (SEC EDGAR 8-K Item 2.02)",
        f"- **CIK:** `{doc['cik']}` | **Filing Date:** {doc['filing_date']}",
        f"- **Accession:** `{doc['accession']}`",
        f"- **Filing Index:** [View on EDGAR]({doc['filing_url']})",
    ]
    if doc.get("primary_url"):
        lines.append(f"- **Primary Document:** [{doc['primary_url'].split('/')[-1]}]({doc['primary_url']})")
    if doc.get("exhibit_url"):
        lines.append(f"- **Exhibit 99.1 (Press Release):** [{doc['exhibit_url'].split('/')[-1]}]({doc['exhibit_url']})")
    lines.append("")

    exhibit_text = doc.get("exhibit_text") or ""
    if exhibit_text:
        lines.append("#### 📰 Press Release Excerpt (Exhibit 99.1):")
        lines.append("```")
        lines.append(exhibit_text)
        lines.append("```")
    elif doc.get("exhibit_url"):
        lines.append(f"*Press release text unavailable — view directly: [{doc['exhibit_url']}]({doc['exhibit_url']})*")
    else:
        lines.append(f"*Exhibit 99.1 URL not resolved from index. View filing directly: [{doc['filing_url']}]({doc['filing_url']})*")

    return "\n".join(lines)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else "AVGO"
    print(format_sec_report(test_ticker))
