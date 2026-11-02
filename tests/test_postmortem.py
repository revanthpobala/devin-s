from datetime import date, timedelta

import pandas as pd

from src.logic import postmortem as pm

DAY = date(2026, 10, 30)


def _closes(ticker_drop, peer_ret, spy_ret, n_peers=4):
    days = [DAY - timedelta(days=k) for k in range(40, -1, -1)]
    cols = {"INTC": [], "SPY": [], "AAA": []}
    for k in range(n_peers):
        cols[f"P{k}"] = []
    for d in days:
        last = d == DAY
        base = 100.0
        cols["INTC"].append(base * (1 + ticker_drop) if last else base)
        cols["SPY"].append(base * (1 + spy_ret) if last else base)
        cols["AAA"].append(base * (1 + ticker_drop) if last else base)
        for k in range(n_peers):
            cols[f"P{k}"].append(base * (1 + peer_ret) if last else base)
    return pd.DataFrame(cols, index=pd.to_datetime(days))


PROFILES = {"INTC": [0, {"industry": "Semis"}], **{f"P{k}": [0, {"industry": "Semis"}] for k in range(4)},
            "AAA": [0, {"industry": "Software"}]}


def test_label_rules_in_order():
    assert pm.label_move(-0.3, 0.0, -0.05, True) == "EARNINGS"
    assert pm.label_move(-0.3, -0.04, -0.05, False) == "MARKET"
    assert pm.label_move(-0.3, 0.0, -0.05, False) == "INDUSTRY"
    assert pm.label_move(-0.3, 0.0, -0.01, False) == "IDIOSYNCRATIC"


def test_industry_drop_ranks_peers_and_has_no_llm(monkeypatch):
    card = pm.build_card("INTC", DAY, closes=_closes(-0.30, -0.05, 0.0), profiles=PROFILES, ledger=[], eps={}, llm=False)
    assert card["label"] == "INDUSTRY"
    assert card["move_pct"] == -30.0 and card["industry_ex_self_pct"] == -5.0
    assert card["peers_worst"][0][1] == -5.0 and len(card["peers_worst"]) == 4
    assert card["prior_close"]["run21_pct"] == 0.0 and "why" not in card


def test_earnings_day_from_ledger_wins_over_market(monkeypatch):
    ledger = [{"ticker": "INTC", "report_date": "2026-10-30", "implied_move_pct": 9.0, "own_median_pct": 7.85}]
    card = pm.build_card("INTC", DAY, closes=_closes(-0.30, -0.05, -0.05), profiles=PROFILES, ledger=ledger, eps={}, llm=False)
    assert card["label"] == "EARNINGS" and card["ledger"]["implied_move_pct"] == 9.0


def test_idiosyncratic_drop_with_no_peers(monkeypatch):
    card = pm.build_card("AAA", DAY, closes=_closes(-0.15, 0.0, 0.0), profiles=PROFILES, ledger=[], eps={}, llm=False)
    assert card["label"] == "IDIOSYNCRATIC" and card["peers_worst"] == []


def test_surprise_match_within_window():
    eps = {"INTC": {"quarters": [{"reportedDate": "2026-10-29", "surprisePercentage": "-50.0"}]}}
    s = pm._eps_match("INTC", DAY, eps)
    assert s["bucket"] == "MISS" and s["gap"] == 1
    assert pm._eps_match("INTC", date(2026, 7, 1), eps) is None


def test_select_8k_picks_item_202_filed_on_or_day_before():
    subs = {"filings": {"recent": {
        "form": ["8-K", "8-K", "10-Q"], "filingDate": ["2026-10-29", "2026-10-30", "2026-10-30"],
        "items": ["2.02,9.01", "5.02", "2.02"], "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm"]}}}
    hit = pm.select_8k(subs, DAY)
    assert hit == {"accession": "0001-26-1", "primaryDocument": "a.htm", "filingDate": "2026-10-29"}
    assert pm.select_8k(subs, date(2026, 9, 1)) is None


def test_find_exhibit_url_prefers_99_1():
    base = "https://www.sec.gov/Archives/edgar/data/50863/000005086326000001/0000050863-26-000001-index.htm"
    html = '<a href="/Archives/x/a.htm">a</a><a href="/Archives/x/ex99-1.htm">EX-99.1</a>'
    assert pm.find_exhibit_url(html, base) == "https://www.sec.gov/Archives/x/ex99-1.htm"
    assert pm.find_exhibit_url("<a href='a.htm'>x</a>", base) is None


def test_explain_uses_filing_text_and_llm(monkeypatch):
    import src.clients.llm_client as llm

    seen = {}

    def fake_llm(system_prompt, user_prompt="", **kw):
        seen["user"] = user_prompt
        return "- Missed revenue\n- Guidance cut\n- Foundry delays"

    monkeypatch.setattr(llm, "query_local_llm", fake_llm)
    out = pm.explain("INTC", DAY, "EARNINGS", lambda t, d: "Revenue fell 8%; cut FY guidance.")
    assert out["source"] == "8-K exhibit 99.1" and "Guidance cut" in out["bullets"]
    assert seen["user"].startswith("Revenue fell")


def test_explain_idiosyncratic_falls_back_to_headlines(monkeypatch):
    import src.clients.finnhub_client as fh
    import src.clients.llm_client as llm

    monkeypatch.setattr(fh, "get_company_news", lambda t, days=2: [{"headline": "Probe opened"}])
    monkeypatch.setattr(llm, "query_local_llm", lambda system_prompt, user_prompt="", **kw: "- Probe\n- n/a\n- not stated")
    out = pm.explain("AAA", DAY, "IDIOSYNCRATIC", lambda t, d: None)
    assert out["source"] == "Finnhub headlines (2d)"


def test_notify_card_dedupe_key_and_tier():
    calls = []
    card = {"ticker": "INTC", "date": "2026-10-30", "move_pct": -28.6, "label": "INDUSTRY",
            "market_spy_pct": 0.0, "industry_ex_self_pct": -5.15}
    pm.notify_card(card, notifier=lambda *a, **k: calls.append((a, k)) or True)
    (tier, title, body), kw = calls[0]
    assert tier == "RISK" and kw["dedupe_key"] == "POSTMORTEM:INTC:2026-10-30" and "INDUSTRY" in title


def test_scan_writes_card_and_notifies_once(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "REPORTS", tmp_path)
    monkeypatch.setattr(pm, "_load_profiles", lambda: PROFILES)
    monkeypatch.setattr(pm, "_names", lambda db, sql: ["INTC"] if "schwab" in db else [])
    monkeypatch.setattr(pm, "fetch_filing_text", lambda t, d: None)
    import src.clients.finnhub_client as fh

    monkeypatch.setattr(fh, "get_company_news", lambda t, days=2: [])
    sent = []
    res = pm.scan(DAY, notifier=lambda *a, **k: sent.append(k["dedupe_key"]) or True,
                  closes=_closes(-0.30, -0.15, 0.0))
    assert res["hits"] == 6
    assert (tmp_path / "2026-10-30" / "INTC_postmortem.md").is_file()
    assert (tmp_path / "2026-10-30" / "P0_postmortem.md").is_file()
    assert sent == ["POSTMORTEM:INTC:2026-10-30"]


def test_scan_skips_a_failing_card(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "REPORTS", tmp_path)
    monkeypatch.setattr(pm, "_load_profiles", lambda: PROFILES)
    monkeypatch.setattr(pm, "_names", lambda db, sql: [])
    real = pm.build_card

    def flaky(t, day, **kw):
        if t == "INTC":
            raise KeyError(t)
        return real(t, day, closes=_closes(-0.30, -0.15, 0.0), ledger=[], eps={}, **{**kw, "llm": False})

    monkeypatch.setattr(pm, "build_card", flaky)
    res = pm.scan(DAY, notifier=lambda *a, **k: True, closes=_closes(-0.30, -0.15, 0.0))
    assert "INTC" not in [c["ticker"] for c in res["cards"]] and len(res["cards"]) == 5


def test_recent_card_block_finds_latest_within_window(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "REPORTS", tmp_path)
    d = tmp_path / "2026-10-30"
    d.mkdir()
    (d / "INTC_postmortem.md").write_text("# INTC post-mortem 2026-10-30: -28.6% (INDUSTRY)\n")
    assert "INDUSTRY" in pm.recent_card_block("INTC", today=date(2026, 11, 10))
    assert pm.recent_card_block("INTC", today=date(2026, 12, 30)) == ""
