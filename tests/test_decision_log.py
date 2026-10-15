import pytest

from src.tracking import decision_log as dl


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "db_path", lambda: tmp_path / "d.db")


def test_record_latest_and_summary(store):
    dl.record(7, "aapl", "2026-10-01", "SKIP", "rr_too_low", "ACT", 2.4, 200.0)
    dl.record(7, "AAPL", "2026-10-01", "TAKE", None, "ACT", 2.4, 200.0)
    dl.record(9, "MSFT", "2026-10-01", "SKIP", "NO_CASH", "WAIT", None, 500.0)
    assert dl.latest_by_plan()[7]["decision"] == "TAKE"
    s = dl.summary()
    assert s["n"] == 3 and s["by_system"]["ACT"] == {"SKIP:RR_TOO_LOW": 1, "TAKE": 1}


def test_bad_inputs_rejected(store):
    with pytest.raises(ValueError):
        dl.record(1, "X", "d", "MAYBE", None, None, None, None)
    with pytest.raises(ValueError):
        dl.record(1, "X", "d", "SKIP", "BECAUSE", None, None, None)


def test_score_counts_plan_outcomes():
    from src.tracking.decision_log import score
    rows = [{"id": 1, "status": "TARGET_HIT"}, {"id": 2, "status": "INVALIDATED"}, {"id": 3, "status": "STALKING"}]
    s = score(rows, [1, 2], [3, 9])
    assert s["skipped"] == {"would_have_won": 1, "would_have_lost": 1, "open": 0}
    assert s["agent_passed"]["open"] == 1
