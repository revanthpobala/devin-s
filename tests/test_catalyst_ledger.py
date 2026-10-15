from src.tracking import catalyst_ledger as cl

BOOK = {"equity": {"AMZN": 255.0, "KO": 70.0}, "short_calls": [{"ticker": "AMZN", "strike": 250.0, "expiry": "2026-11-20", "qty": 1}]}


def test_earnings_t5_t1_and_call_spanning_catalyst():
    cats = [{"ticker": "AMZN", "kind": "EARNINGS", "date": "2026-10-29"}, {"ticker": "KO", "kind": "EX_DIV", "date": "2026-11-28"}]
    assert [e["key"] for e in cl.catalyst_events("2026-10-20", BOOK, cats)] == ["CAT:AMZN:EARNINGS:2026-10-29:CC250:2026-11-20"]
    ev = cl.catalyst_events("2026-10-28", BOOK, cats)
    assert {e["severity"] for e in ev} == {"WARN"} and len(ev) == 2
    assert any("T1" in e["key"] for e in ev) and any("before your $250 call" in e["title"] for e in ev)
    t5 = [e for e in cl.catalyst_events("2026-10-24", BOOK, cats) if e["key"].endswith("T5")]
    assert len(t5) == 1 and t5[0]["severity"] == "INFO"


def test_exdiv_warns_only_when_call_is_in_the_money():
    cats = [{"ticker": "AMZN", "kind": "EX_DIV", "date": "2026-11-10"}]
    assert len(cl.catalyst_events("2026-11-01", BOOK, cats)) == 1
    otm = {**BOOK, "equity": {"AMZN": 240.0}}
    assert cl.catalyst_events("2026-11-01", otm, cats) == []


def _bars(closes, opens=None):
    return [{"date": f"d{i}", "open": (opens or closes)[i], "close": c} for i, c in enumerate(closes)]


def test_classify_gap_vs_run():
    gap = cl.classify_move(_bars([100, 100, 100, 100, 100, 118], [100, 100, 100, 100, 100, 117]))
    assert gap["dir"] == "UP" and gap["gap_driven"] == 1
    run = cl.classify_move(_bars([100, 103, 106, 109, 112, 115], [100, 101, 104, 107, 110, 113]))
    assert run["gap_driven"] == 0 and run["move_pct"] == 15.0
    assert cl.classify_move(_bars([100, 101, 102, 103, 104, 105])) is None
    assert cl.classify_move(_bars([100, 100, 100, 100, 100, 85]))["dir"] == "DOWN"


def test_mover_events_flag_only_big_moves_and_say_what_to_do():
    from src.tracking import catalyst_ledger as cl

    book = {"equity": {"HELD": 100.0}, "short_calls": [{"ticker": "HELD", "strike": 105.0, "expiry": "2026-10-23", "qty": 1}]}
    ev = cl.mover_events("2026-10-14", {"HELD": 6.0, "OTHER": 5.0, "SMALL": 1.0, "QUIET": 3.2},
                         {"HELD": 2.0, "OTHER": 2.0, "SMALL": 0.3, "QUIET": 3.0}, book, [], {})
    by = {e["ticker"]: e for e in ev}
    assert set(by) == {"HELD", "OTHER"}
    assert by["HELD"]["severity"] == "WARN" and by["HELD"]["title"].startswith("REVIEW HELD") and "short $105 call" in by["HELD"]["body"]
    assert by["OTHER"]["severity"] == "INFO" and "NOT HELD" in by["OTHER"]["body"] and "wait for a zone" in by["OTHER"]["body"]


def test_daily_sigma():
    from src.tracking import catalyst_ledger as cl

    bars, px = [], 100.0
    for i in range(30):
        px *= 1.02 if i % 2 else 0.98
        bars.append({"close": px})
    assert cl.daily_sigma(bars[:5]) is None
    assert 1.0 < cl.daily_sigma(bars) < 3.0


def test_index_move_alerts_at_lower_threshold():
    from src.tracking import catalyst_ledger as cl

    book = {"equity": {}, "short_calls": []}
    ev = cl.mover_events("2026-10-14", {"QQQ": -2.0, "XYZ": -2.0}, {"QQQ": 1.0, "XYZ": 1.0}, book, [], {})
    assert [e["ticker"] for e in ev] == ["QQQ"] and ev[0]["title"].startswith("MARKET QQQ") and ev[0]["severity"] == "WARN"
