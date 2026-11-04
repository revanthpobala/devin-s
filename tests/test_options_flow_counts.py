from types import SimpleNamespace

from src.clients import schwab_client as sc


def _contract(vol, oi, last_size, last=5.0):
    return {"totalVolume": vol, "openInterest": oi, "lastSize": last_size, "last": last, "bid": last - 0.05, "ask": last + 0.05,
            "daysToExpiration": 10, "delta": 0.5, "tradeTimeInLong": 1793740481066}


def _run(monkeypatch, calls, puts):
    chain = {"status": "SUCCESS", "underlyingPrice": 100.0,
             "callExpDateMap": {"2026-11-20:10": {str(100 + i): [c] for i, c in enumerate(calls)}},
             "putExpDateMap": {"2026-11-20:10": {str(90 + i): [c] for i, c in enumerate(puts)}}}
    resp = SimpleNamespace(status_code=200, json=lambda: chain, text="")
    client = SimpleNamespace(Options=SimpleNamespace(ContractType=SimpleNamespace(ALL="ALL")), get_option_chain=lambda *a, **k: resp)
    monkeypatch.setattr(sc, "get_schwab_client", lambda: client)
    sc._OPTIONS_FLOW_CACHE.clear()
    return sc.get_unusual_options_flow_data("ZZZ", force_refresh=True)


def test_zero_volume_prints_are_not_flow(monkeypatch):
    r = _run(monkeypatch, [_contract(0, 2800, 26)], [_contract(0, 400, 15)])
    assert r["total_anomalies_count"] == 0 and r["tape_count"] == 0
    assert r["sentiment"] == "NEUTRAL"


def test_only_sweeps_and_blocks_are_counted(monkeypatch):
    r = _run(monkeypatch, [_contract(3000, 500, 150), _contract(900, 5000, 30)], [_contract(40, 2000, 25)])
    assert r["tape_count"] == 3
    assert r["total_anomalies_count"] == 1 and r["call_sweeps_count"] == 1 and r["put_sweeps_count"] == 0
