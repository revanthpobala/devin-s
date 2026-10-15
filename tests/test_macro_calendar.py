from datetime import datetime
from zoneinfo import ZoneInfo

from src.tracking.macro_calendar import macro_events

ET = ZoneInfo("America/New_York")
EVENTS = [{"title": "PPI m/m", "at": "2026-10-15T08:30:00-04:00"}, {"title": "Retail Sales m/m", "at": "2026-10-15T08:30:00-04:00"}]


def _opt(exp, und="$NDX"):
    return {"asset_type": "OPTION", "quantity": 1, "underlying_symbol": und, "option_expiration": exp}


def test_one_alert_per_release_with_same_day_expiry_marked_warn():
    out = macro_events(datetime(2026, 10, 14, 20, 0, tzinfo=ET), EVENTS, [_opt("2026-10-15"), _opt("2026-10-15")])
    assert len(out) == 1
    assert out[0]["severity"] == "WARN" and out[0]["key"].endswith(":24h")
    assert "PPI m/m, Retail Sales m/m" in out[0]["title"] and "(2 legs)" in out[0]["body"]


def test_hour_before_gets_its_own_key_and_outside_window_is_silent():
    assert macro_events(datetime(2026, 10, 15, 7, 45, tzinfo=ET), EVENTS, [_opt("2026-10-15")])[0]["key"].endswith(":1h")
    assert macro_events(datetime(2026, 10, 13, 8, 0, tzinfo=ET), EVENTS, [_opt("2026-10-15")]) == []


def test_expiry_within_a_week_is_info_and_far_expired_or_no_options_are_ignored():
    now = datetime(2026, 10, 14, 20, 0, tzinfo=ET)
    assert macro_events(now, EVENTS, [_opt("2026-10-20")])[0]["severity"] == "INFO"
    assert macro_events(now, EVENTS, [_opt("2027-01-15")]) == []
    assert macro_events(now, EVENTS, [_opt("2026-10-14")]) == []
    assert macro_events(now, EVENTS, [{"asset_type": "EQUITY", "quantity": 10}]) == []


def test_board_lists_exposure_for_releases_inside_horizon_only():
    from src.tracking.macro_calendar import release_groups
    g = release_groups(datetime(2026, 10, 14, 20, 0, tzinfo=ET), EVENTS, [_opt("2026-10-15")])
    assert len(g) == 1 and g[0]["same_day"] and g[0]["pre_open"]
    assert g[0]["exposed"] == [{"underlying": "$NDX", "expiry": "2026-10-15", "legs": 1}]
    assert release_groups(datetime(2026, 10, 10, 20, 0, tzinfo=ET), EVENTS, []) == []


def test_playbook_covers_inflation_and_sales_once_each():
    from src.tracking.macro_calendar import release_groups
    ev = EVENTS + [{"title": "Core PPI m/m", "at": "2026-10-15T08:30:00-04:00"}]
    g = release_groups(datetime(2026, 10, 14, 20, 0, tzinfo=ET), ev, [])
    assert [x["release"] for x in g[0]["playbook"]] == ["PPI", "Retail Sales"]


def test_llm_context_states_exposure_and_forbids_direction_calls():
    from src.tracking.macro_calendar import context_text
    txt = context_text(datetime(2026, 10, 14, 20, 0, tzinfo=ET), EVENTS, [_opt("2026-10-15")])
    assert "PPI m/m" in txt and "$NDX exp 2026-10-15" in txt and "do not predict direction" in txt
    assert context_text(datetime(2026, 10, 10, 20, 0, tzinfo=ET), EVENTS, []) == ""


def test_release_after_the_close_does_not_hit_options_expiring_that_day():
    from src.tracking.macro_calendar import release_groups
    late = [{"title": "Fed Chairman Warsh Speaks", "at": "2026-10-15T23:30:00-04:00"}]
    now = datetime(2026, 10, 14, 20, 0, tzinfo=ET)
    g = release_groups(now, late, [_opt("2026-10-15"), _opt("2026-10-16")])
    assert g[0]["same_day"] is False
    assert g[0]["exposed"] == [{"underlying": "$NDX", "expiry": "2026-10-16", "legs": 1}]
