"""
macro_client.py
---------------
Pre-fetches real-time macro/tape context before every LLM call so that
the local Qwen model can execute STEP 2 (Catalyst Check) of revanth-0dte.md.

Sources (all FREE):
  - Finnhub  : market news (general + ticker + megacaps), earnings calendar,
                market status, live quote
  - FMP      : ^VIX live quote, biggest gainers/losers
  - Python   : FOMC calendar (hardcoded Fed schedule), OPEX calculator,
                time-of-day session label, VIX spike flags
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# ── 60-second in-process cache ────────────────────────────────────────────────
_cache: dict = {}
_cache_ts: dict = {}


def _cached_get(url: str, headers: dict | None = None, ttl: int = 60):
    now = datetime.now(timezone.utc).timestamp()
    if url in _cache and (now - _cache_ts.get(url, 0)) < ttl:
        return _cache[url]
    try:
        r = requests.get(url, headers=headers or {}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            _cache[url] = data
            _cache_ts[url] = now
            return data
    except Exception as e:
        logger.warning(f"macro_client fetch [{url[:60]}]: {e}")
    return None


def _fh():
    return os.getenv("FINHUB_API_KEY", "")


def _fmp():
    return os.getenv("FIN_MODEL_PREP_API_KEY", "")


ET = ZoneInfo("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# 1. TIME-OF-DAY SESSION LABEL  (gem §STEP 2 D + §TABLE E)
# ─────────────────────────────────────────────────────────────────────────────


def get_session_label() -> str:
    """Returns the current 0DTE session window with gem-specific guidance."""
    now = datetime.now(ET)
    h, m = now.hour, now.minute
    t = h * 60 + m  # minutes since midnight ET

    if t < 9 * 60 + 30:
        label = "PRE-MARKET — no 0DTE entries yet"
    elif t < 10 * 60:
        label = "9:30–10:00 OPENING DRIVE — let OR set; A-grade ignition only"
    elif t < 11 * 60 + 30:
        label = "10:00–11:30 PRIME TREND WINDOW — full menu, best window"
    elif t < 13 * 60 + 30:
        label = "11:30–13:30 LUNCH CHOP — demand A-grade; default skip B"
    elif t < 15 * 60:
        label = "13:30–15:00 AFTERNOON TREND — tradeable; mind pre-event drift"
    elif t < 15 * 60 + 45:
        label = "15:00–15:45 LATE — card blocks new entries; MANAGE ONLY"
    else:
        label = "≥15:45 EOD — card force-flats; NO POSITIONS HELD"

    return f"Session: {label}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. FOMC CALENDAR  (hardcoded Fed schedule — updated annually)
#    Source: federalreserve.gov/monetarypolicy/fomccalendars.htm
# ─────────────────────────────────────────────────────────────────────────────

# 2026 FOMC decision dates (2:00 PM ET) — update each January
FOMC_DATES_2026 = [
    date(2026, 1, 29),
    date(2026, 3, 19),
    date(2026, 5, 7),
    date(2026, 6, 18),
    date(2026, 7, 30),
    date(2026, 9, 17),
    date(2026, 10, 29),
    date(2026, 12, 10),
]


def get_fomc_alert() -> str:
    """
    Returns a FOMC warning if:
    - Today IS a FOMC day (with minutes-until flag if within 2h of 14:00 ET)
    - A FOMC meeting is tomorrow
    """
    now_et = datetime.now(ET)
    today = now_et.date()
    lines = []

    for fd in FOMC_DATES_2026:
        delta = (fd - today).days
        if delta == 0:
            # It's FOMC day — how many minutes until 14:00 ET?
            fomc_time = datetime(fd.year, fd.month, fd.day, 14, 0, tzinfo=ET)
            mins_until = int((fomc_time - now_et).total_seconds() / 60)
            if mins_until > 0:
                if mins_until <= 90:
                    lines.append(
                        f"⚠️  FOMC DECISION IN {mins_until} MIN (14:00 ET) — "
                        f"DO NOT BUY PREMIUM — WAIT UNTIL AFTER 14:30"
                    )
                else:
                    h_left = mins_until // 60
                    m_left = mins_until % 60
                    lines.append(
                        f"📅 FOMC DAY — decision at 14:00 ET "
                        f"(in {h_left}h {m_left}m) + Powell presser 14:30. "
                        f"Avoid premium <90 min before."
                    )
            else:
                # After 14:00 — Powell presser window
                mins_after = abs(mins_until)
                if mins_after <= 60:
                    lines.append(
                        f"🔔 FOMC decision released {mins_after}m ago — "
                        f"Powell presser in progress or just ended. "
                        f"Trade reaction carefully."
                    )
        elif delta == 1:
            lines.append(
                f"📅 FOMC tomorrow ({fd.strftime('%b %d')}) — elevated IV today; be cautious with premium."
            )

    return "\n".join(lines) if lines else ""


# ─────────────────────────────────────────────────────────────────────────────
# 3. OPEX CALCULATOR  (gem §STEP 2 A — "gamma pin risk")
# ─────────────────────────────────────────────────────────────────────────────


def _third_friday(y: int, m: int) -> date:
    """Return the third Friday of the given year/month."""
    first = date(y, m, 1)
    # Weekday: Monday=0 … Friday=4
    offset = (4 - first.weekday()) % 7  # days to first Friday
    return first + timedelta(days=offset + 14)


def get_opex_alert() -> str:
    """Returns OPEX warning if today is monthly or quarterly OpEx."""
    today = date.today()
    monthly_opex = _third_friday(today.year, today.month)

    if today == monthly_opex:
        # Quarterly OpEx: March, June, September, December
        if today.month in (3, 6, 9, 12):
            return (
                "⚠️  QUARTERLY OpEx TODAY (Triple Witching) — "
                "gamma pin risk, elevated vol/whip, unpredictable closes."
            )
        return (
            "📅 MONTHLY OpEx TODAY — gamma pin risk near round strikes; late-day pinning possible."
        )

    days_to_opex = (monthly_opex - today).days
    if 1 <= days_to_opex <= 2:
        return f"📅 OpEx in {days_to_opex} day(s) ({monthly_opex.strftime('%b %d')}) — mild gamma-pin awareness."

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. VIX  (live level + regime flag per gem rules)
# ─────────────────────────────────────────────────────────────────────────────


def get_vix() -> str:
    key = _fmp()
    if not key:
        return "VIX: N/A"
    data = _cached_get(f"https://financialmodelingprep.com/stable/quote?symbol=^VIX&apikey={key}")
    if data and isinstance(data, list) and data:
        v = data[0]
        price = float(v.get("price", 0))
        chg = float(v.get("changePercentage", 0))

        if price > 30:
            flag = "🔴 SPIKE >30 — STAND ASIDE (gem rule §6)"
        elif price > 25:
            flag = "⚠️ ELEVATED >25 — HALF SIZE (gem rule §6)"
        elif price > 20:
            flag = "⚠️ ELEVATED >20 — reduce size, heightened risk"
        elif chg > 10:
            flag = "⚠️ SPIKING intraday — event possibly in progress"
        else:
            flag = "✅ normal range"

        return f"VIX: {price:.2f} ({chg:+.2f}%)  [{flag}]"
    return "VIX: N/A"


# ─────────────────────────────────────────────────────────────────────────────
# 5. LIVE QUOTE  (Finnhub)
# ─────────────────────────────────────────────────────────────────────────────


def get_live_quote(symbol: str) -> str:
    key = _fh()
    if not key:
        return ""
    data = _cached_get(f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={key}")
    if data and data.get("c"):
        c, o, h, l, pc = data["c"], data["o"], data["h"], data["l"], data["pc"]
        chg_pct = ((c - pc) / pc * 100) if pc else 0
        return f"{symbol}: ${c:.2f} ({chg_pct:+.2f}%)  H:{h:.2f} L:{l:.2f}  Open:{o:.2f}"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 6. EARNINGS CALENDAR  (Finnhub — with ER flag for the alerted ticker)
# ─────────────────────────────────────────────────────────────────────────────


def get_todays_earnings(ticker: str | None = None) -> str:
    key = _fh()
    if not key:
        return ""
    end = datetime.now(ET)
    start = end - timedelta(days=1)
    from_date = start.strftime("%Y-%m-%d")
    to_date = end.strftime("%Y-%m-%d")
    data = _cached_get(
        f"https://finnhub.io/api/v1/calendar/earnings?from={from_date}&to={to_date}&token={key}", ttl=300
    )
    if not data:
        return ""
    items = data.get("earningsCalendar", [])
    if not items:
        return "Earnings (recent/scheduled): none found"

    lines = []
    er_flag = ""

    for e in items[:15]:
        sym = e.get("symbol", "?")
        e_date = e.get("date", "")
        hour = {"bmo": "pre-mkt ✅ released", "amc": "after-mkt", "dmh": "DURING MKT ⚠️"}.get(
            e.get("hour", ""), e.get("hour", "?")
        )
        eps_est = e.get("epsEstimate")
        est_str = f"  EPS est: {eps_est:.2f}" if eps_est is not None else ""

        # Flag if the alerted ticker itself has earnings
        if ticker and sym.upper() == ticker.upper():
            er_flag = (
                f"🚨 ER ALERT: {sym} reports earnings ({e_date} {hour}) — "
                f"IV crush + binary risk. Gem rule: prefer debit spread, not naked."
            )

        lines.append(f"  {sym} [{e_date}] ({hour}){est_str}")

    result = "Earnings calendar (recent/today):\n" + "\n".join(lines)
    if er_flag:
        result = er_flag + "\n\n" + result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 7. NEWS — ticker + megacaps + general tape  (Finnhub free)
#    Gem §STEP 2 B: "catalyst is usually macro or a megacap headline"
# ─────────────────────────────────────────────────────────────────────────────

MEGACAPS = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO"]


def _fetch_company_news(symbol: str, max_items: int = 3, days: int = 2) -> list[str]:
    """Return formatted headline lines for a symbol (last N days)."""
    key = _fh()
    if not key:
        return []
    end = datetime.now(ET)
    start = end - timedelta(days=days)
    from_date = start.strftime("%Y-%m-%d")
    to_date = end.strftime("%Y-%m-%d")
    data = _cached_get(
        f"https://finnhub.io/api/v1/company-news?symbol={symbol}&from={from_date}&to={to_date}&token={key}"
    )
    if not data or not isinstance(data, list):
        return []

    lines = []
    seen = set()
    for item in data:
        headline = item.get("headline", "").strip()
        source = item.get("source", "")
        ts = item.get("datetime", 0)
        if not headline or headline in seen:
            continue
        seen.add(headline)
        try:
            time_str = datetime.fromtimestamp(ts, tz=ET).strftime("%H:%M ET")
        except Exception:
            time_str = "?"
        lines.append(f"  [{time_str}] {headline}  ({source})")
        if len(lines) >= max_items:
            break
    return lines


def get_ticker_news(ticker: str) -> str:
    lines = _fetch_company_news(ticker, max_items=4)
    if not lines:
        return ""
    return f"News ({ticker} today):\n" + "\n".join(lines)


def get_megacap_news() -> str:
    """Scan AAPL/NVDA/MSFT/AMZN/GOOGL/META for today's headlines."""
    all_lines = []
    for sym in MEGACAPS:
        lines = _fetch_company_news(sym, max_items=2)
        if lines:
            all_lines.append(f"  {sym}:")
            all_lines.extend(["  " + l for l in lines])
    if not all_lines:
        return ""
    return "Megacap headlines (AAPL/NVDA/MSFT/AMZN/GOOGL/META):\n" + "\n".join(all_lines)


def get_general_news(max_items: int = 4) -> str:
    key = _fh()
    if not key:
        return ""
    data = _cached_get(f"https://finnhub.io/api/v1/news?category=general&token={key}")
    if not data or not isinstance(data, list):
        return ""

    lines = []
    seen = set()
    for item in data:
        headline = item.get("headline", "").strip()
        source = item.get("source", "")
        ts = item.get("datetime", 0)
        if not headline or headline in seen:
            continue
        seen.add(headline)
        try:
            time_str = datetime.fromtimestamp(ts, tz=ET).strftime("%H:%M ET")
        except Exception:
            time_str = "?"
        lines.append(f"  [{time_str}] {headline}  ({source})")
        if len(lines) >= max_items:
            break

    return ("Market news (tape):\n" + "\n".join(lines)) if lines else ""


def get_sector_category_news(max_items: int = 6) -> str:
    """Fetch general market and sector news categorized by domain (Tech, Financials, Energy, Macro) without needing tickers."""
    lines_by_sector: dict[str, list[str]] = {}

    # 1. Try Finnhub general news first (free API key already in environment)
    key_fh = _fh()
    if key_fh:
        data = _cached_get(f"https://finnhub.io/api/v1/news?category=general&token={key_fh}", ttl=180)
        if data and isinstance(data, list):
            for item in data:
                headline = item.get("headline", "").strip()
                source = item.get("source", "")
                ts = item.get("datetime", 0)
                text = (headline + " " + item.get("summary", "")).lower()

                try:
                    time_str = datetime.fromtimestamp(ts, tz=ET).strftime("%H:%M ET")
                except Exception:
                    time_str = "?"

                # Sector/Domain classification
                sec = "Macro / Tape"
                if any(w in text for w in ["tech", "ai", "chip", "semiconductor", "software", "cloud", "apple", "nvidia"]):
                    sec = "Technology"
                elif any(w in text for w in ["bank", "fed", "rate", "yield", "inflation", "cpi", "treasury", "sec"]):
                    sec = "Financials & Fed"
                elif any(w in text for w in ["oil", "energy", "gas", "crude", "opec", "pipeline"]):
                    sec = "Energy"
                elif any(w in text for w in ["drug", "pharma", "fda", "health", "biotech", "vaccine"]):
                    sec = "Healthcare"

                if sec not in lines_by_sector:
                    lines_by_sector[sec] = []
                if len(lines_by_sector[sec]) < 2:
                    lines_by_sector[sec].append(f"  [{time_str}] ({sec}) {headline} — {source}")

    if not lines_by_sector:
        return ""

    formatted = ["Sector & Category Headlines (Tape):"]
    for sec, items in lines_by_sector.items():
        formatted.extend(items)
    return "\n".join(formatted[:max_items + 1])


# ─────────────────────────────────────────────────────────────────────────────
# 8. BIGGEST MOVERS  (FMP free)
# ─────────────────────────────────────────────────────────────────────────────


def get_biggest_movers() -> str:
    key = _fmp()
    if not key:
        return ""
    gainers = _cached_get(
        f"https://financialmodelingprep.com/stable/biggest-gainers?apikey={key}", ttl=120
    )
    losers = _cached_get(
        f"https://financialmodelingprep.com/stable/biggest-losers?apikey={key}", ttl=120
    )

    lines = []
    if gainers and isinstance(gainers, list):
        lines.append("Top gainers:")
        for g in gainers[:3]:
            lines.append(
                f"  {g.get('symbol', '?')} +{g.get('changesPercentage', 0):.1f}%  {g.get('name', '')[:30]}"
            )
    if losers and isinstance(losers, list):
        lines.append("Top losers:")
        for l in losers[:3]:
            lines.append(
                f"  {l.get('symbol', '?')} {l.get('changesPercentage', 0):.1f}%  {l.get('name', '')[:30]}"
            )

    return "\n".join(lines) if lines else ""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY — build full STEP 2 context block
# ─────────────────────────────────────────────────────────────────────────────


def build_macro_context(ticker: str | None = None) -> str:
    """
    Assembles the complete STEP 2 Catalyst Check block from revanth-0dte.md.
    Inject this into the LLM user_prompt before every alert analysis.
    """
    sections = []

    # ── A) Time-of-day session ────────────────────────────────────────────────
    sections.append(get_session_label())

    # ── B) FOMC warning ──────────────────────────────────────────────────────
    fomc = get_fomc_alert()
    if fomc:
        sections.append(fomc)

    # ── C) OPEX ──────────────────────────────────────────────────────────────
    opex = get_opex_alert()
    if opex:
        sections.append(opex)

    # ── D) VIX with regime flag ───────────────────────────────────────────────
    sections.append(get_vix())

    # ── E) Live quote for the alerted ticker ─────────────────────────────────
    if ticker:
        q = get_live_quote(ticker)
        if q:
            sections.append(f"Live quote — {q}")

    # ── F) Earnings today (with ER flag for alerted ticker) ──────────────────
    earnings = get_todays_earnings(ticker=ticker)
    if earnings:
        sections.append(earnings)

    # ── G) Ticker-specific news ───────────────────────────────────────────────
    if ticker:
        tnews = get_ticker_news(ticker)
        if tnews:
            sections.append(tnews)

    # ── H) Megacap headlines (AAPL/NVDA/MSFT/AMZN/GOOGL/META/TSLA/AVGO) ───────
    mega = get_megacap_news()
    if mega:
        sections.append(mega)

    # ── I) Sector & Category headlines (Technology, Financials, Energy, etc.) ──
    sec_news = get_sector_category_news(max_items=6)
    if sec_news:
        sections.append(sec_news)

    # ── J) General tape / macro news ─────────────────────────────────────────
    general = get_general_news(max_items=4)
    if general:
        sections.append(general)

    # ── K) Biggest movers ────────────────────────────────────────────────────
    movers = get_biggest_movers()
    if movers:
        sections.append(movers)

    body = "\n\n".join(s for s in sections if s)
    return f"=== STEP 2: MACRO / TAPE CONTEXT ===\n{body}\n=== END STEP 2 ==="
