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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
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
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    facts_data = _fetch_url_json(facts_url, headers=SEC_HEADERS)
    quarterly_financials = []

    if facts_data and "facts" in facts_data:
        us_gaap = facts_data["facts"].get("us-gaap", {})

        def _get_metric_units(concept_names: list[str]) -> list:
            for name in concept_names:
                if name in us_gaap:
                    units = us_gaap[name].get("units", {})
                    if "USD" in units:
                        return units["USD"]
            return []

        rev_units = _get_metric_units([
            "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueGoodsNet", "OperatingRevenue"
        ])
        net_inc_units = _get_metric_units(["NetIncomeLoss", "ProfitLoss"])
        op_inc_units = _get_metric_units(["OperatingIncomeLoss"])
        cash_units = _get_metric_units([
            "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
        ])
        debt_units = _get_metric_units(["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"])

        # Organize by (fy, fp) or end date
        quarters = {}

        def _ingest_units(units: list, metric_name: str, is_instant: bool = False):
            # Sort by end date descending
            sorted_units = sorted([u for u in units if u.get("form") in ("10-Q", "10-K") and u.get("val") is not None],
                                  key=lambda x: str(x.get("end", "")), reverse=True)
            for u in sorted_units[:12]:
                end = u.get("end", "")
                val = u.get("val")
                form = u.get("form", "")
                fp = u.get("fp", "")
                fy = u.get("fy", "")
                q_key = end
                if q_key not in quarters:
                    quarters[q_key] = {"end_date": end, "form": form, "fy": fy, "fp": fp}
                if metric_name not in quarters[q_key]:
                    quarters[q_key][metric_name] = val

        _ingest_units(rev_units, "revenue")
        _ingest_units(net_inc_units, "net_income")
        _ingest_units(op_inc_units, "operating_income")
        _ingest_units(cash_units, "cash", is_instant=True)
        _ingest_units(debt_units, "long_term_debt", is_instant=True)

        sorted_quarters = sorted(quarters.values(), key=lambda x: str(x.get("end_date", "")), reverse=True)
        quarterly_financials = sorted_quarters[:4]

    return {
        "ticker": ticker_clean,
        "company_name": company_name,
        "cik": cik,
        "sic": sic_desc,
        "fiscal_year_end": fiscal_year_end,
        "recent_filings": recent_filings,
        "quarterly_financials": quarterly_financials,
    }


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

    if not data["quarterly_financials"]:
        lines.append("- No XBRL quarterly financial facts available.")
    else:
        lines.append("| Period End | Form | Revenue | Operating Income | Net Income | Cash & Equiv | Long-Term Debt |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for q in data["quarterly_financials"]:
            lines.append(
                f"| {q.get('end_date', 'N/A')} | {q.get('form', 'N/A')} | {_fmt_curr(q.get('revenue'))} | "
                f"{_fmt_curr(q.get('operating_income'))} | {_fmt_curr(q.get('net_income'))} | "
                f"{_fmt_curr(q.get('cash'))} | {_fmt_curr(q.get('long_term_debt'))} |"
            )

    return "\n".join(lines)


def fetch_earnings_press_release(ticker: str) -> str:
    """
    Fetches the official earnings press release (Exhibit 99.1) from the most recent
    8-K filing with Item 2.02 (Results of Operations) on SEC EDGAR.

    Returns a formatted markdown block with:
    - Filing accession number, filing date, and primary document URL
    - Exhibit 99.1 (press release) URL
    - Raw text excerpt (first 4000 chars) of the press release HTML
    """
    import html as html_mod
    import re

    ticker_clean = ticker.strip().upper()
    cik = get_cik_for_ticker(ticker_clean)
    if not cik:
        return f"❌ Could not resolve SEC CIK for {ticker_clean}."

    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    sub_data = _fetch_url_json(sub_url, headers=SEC_HEADERS)
    if not sub_data:
        return f"❌ SEC EDGAR submissions unavailable for {ticker_clean} (CIK {cik})."

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
        return f"❌ No recent 8-K filing found on EDGAR for {ticker_clean}."

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

    lines = [
        f"### 📄 OFFICIAL EARNINGS PRESS RELEASE — {ticker_clean} (SEC EDGAR 8-K Item 2.02)",
        f"- **CIK:** `{cik}` | **Filing Date:** {target_date}",
        f"- **Accession:** `{target_acc}`",
        f"- **Filing Index:** [View on EDGAR]({filing_url})",
    ]
    if primary_url:
        lines.append(f"- **Primary Document:** [{primary_url.split('/')[-1]}]({primary_url})")
    if exhibit_url:
        lines.append(f"- **Exhibit 99.1 (Press Release):** [{exhibit_url.split('/')[-1]}]({exhibit_url})")
    lines.append("")

    if exhibit_text:
        lines.append("#### 📰 Press Release Excerpt (Exhibit 99.1):")
        lines.append("```")
        lines.append(exhibit_text)
        lines.append("```")
    elif exhibit_url:
        lines.append(f"*Press release text unavailable — view directly: [{exhibit_url}]({exhibit_url})*")
    else:
        lines.append(f"*Exhibit 99.1 URL not resolved from index. View filing directly: [{filing_url}]({filing_url})*")

    return "\n".join(lines)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else "AVGO"
    print(format_sec_report(test_ticker))
