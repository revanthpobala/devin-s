from src.logic.layered_entry import build_layers, layers_markdown, layers_text


def test_rklb_2026_10_26_gets_a_starter():
    """RKLB: spot 53.82, zone 52.40-53.37, stop 51.04, T1 63.12. The zone top missed by 20c and the
    stock ran +10%; R:R at market was 3.35, so the plan must start a position now."""
    lay = build_layers(53.82, 52.40, 53.37, 51.04, 63.12, atr=2.5, risk_budget_usd=500)
    assert lay["mode"] == "STARTER_PLUS_LIMITS"
    assert lay["rr_at_market"] == 3.35
    kinds = [t["type"] for t in lay["tranches"]]
    prices = [t["price"] for t in lay["tranches"]]
    assert kinds == ["MARKET", "LIMIT", "LIMIT"]
    assert prices == [53.82, 53.37, 52.40]
    assert lay["tranches"][0]["limit_cap"] == 54.07
    assert lay["risk_usd_if_all_fill"] <= 500
    assert all(t["shares"] == lay["tranches"][0]["shares"] for t in lay["tranches"])


def test_low_rr_at_market_is_limits_only():
    lay = build_layers(107.54, 105.80, 106.99, 104.50, 110.20, atr=2.9, risk_budget_usd=500)
    assert lay["rr_at_market"] < 1.5
    assert lay["mode"] == "LIMITS_ONLY"
    assert [t["type"] for t in lay["tranches"]] == ["LIMIT", "LIMIT", "LIMIT"]
    assert [t["price"] for t in lay["tranches"]] == [106.99, 106.39, 105.80]


def test_earnings_inside_block_window_has_no_starter():
    lay = build_layers(53.82, 52.40, 53.37, 51.04, 63.12, atr=2.5, risk_budget_usd=500,
                       earnings_days=0, earnings_block_days=7)
    assert lay["mode"] == "LIMITS_ONLY"
    assert "earnings" in lay["why"]


def test_spot_inside_zone_only_layers_below_spot():
    lay = build_layers(52.90, 52.40, 53.37, 51.04, 63.12, atr=2.5, risk_budget_usd=500)
    assert [t["type"] for t in lay["tranches"]] == ["MARKET", "LIMIT"]
    assert all(t["price"] <= 52.90 for t in lay["tranches"])


def test_no_layers_for_broken_geometry_or_spot_through_stop():
    assert build_layers(50.90, 52.40, 53.37, 51.04, 63.12, 2.5, 500) is None   # spot under stop
    assert build_layers(64.00, 52.40, 53.37, 51.04, 63.12, 2.5, 500) is None   # spot over T1
    assert build_layers(53.82, 52.40, 53.37, 53.50, 63.12, 2.5, 500) is None   # stop inside zone
    assert build_layers(53.82, None, 53.37, 51.04, 63.12, 2.5, 500) is None    # missing level


def test_averaging_down_never_exceeds_the_risk_budget():
    lay = build_layers(200.0, 190.0, 195.0, 180.0, 260.0, atr=6.0, risk_budget_usd=500)
    assert lay["risk_usd_if_all_fill"] <= 500
    assert lay["total_shares"] == 3 * lay["tranches"][0]["shares"]


def test_text_and_markdown_render():
    lay = build_layers(53.82, 52.40, 53.37, 51.04, 63.12, atr=2.5, risk_budget_usd=500)
    assert layers_text(lay).startswith("LAYERED ")
    md = layers_markdown(lay, "RKLB")
    assert "BUY NOW" in md and "UNMEASURED" in md
    assert "display only" in layers_markdown(lay, "RKLB", rejected=True)


def test_ticket_carries_layers_and_total_risk(monkeypatch):
    from src.logic import order_ticket

    monkeypatch.setattr(order_ticket, "ticket_blocker", lambda *a, **k: None)
    monkeypatch.setattr(order_ticket, "price_option", lambda *a: (None, "no option"))
    sp = {"tactical_stop": 51.04, "target_1": 63.12, "entry_zone_low": 52.40, "entry_zone_high": 53.37}
    sp["layers"] = build_layers(53.82, 52.40, 53.37, 51.04, 63.12, atr=2.5, risk_budget_usd=500)
    w = {"ticker": "RKLB", "side": "LONG", "verdict": "STALK", "actionable": 1, "setup_lane": "JUDGE",
         "levels_source": "llm", "shares_plan": sp, "options_plan": {}}
    t = order_ticket.build_ticket(w, spot=53.82, date_str="2026-10-26")
    assert t["order_type"] == "LAYERED"
    assert t["qty"] == sp["layers"]["total_shares"]
    assert t["risk_usd"] == sp["layers"]["risk_usd_if_all_fill"]
    txt = order_ticket.ticket_text(t)
    assert "now (cap 54.07)" in txt and "@ 53.37" in txt and "@ 52.4" in txt


# --- position target sizing (held names) ---------------------------------------------------

from src.logic.layered_entry import cap_layers_to_position, layers_line


def _googl_layers():
    # GOOGL 2026-10-29 research: limits only (R:R at market 1.28), stop repaired to 258.17
    return build_layers(282.82, 265.64, 268.31, 258.17, 312.38, atr=10.675, risk_budget_usd=500)


def test_held_name_is_sized_to_the_gap_to_target():
    lay = cap_layers_to_position(_googl_layers(), held=191.8084, target=200)
    assert lay["total_shares"] == 8
    assert [t["shares"] for t in lay["tranches"]] == [3, 3, 2]
    assert lay["tranches"][0]["price"] == 268.31
    assert "200" in lay["why"] and "add 8" in lay["position_note"]


def test_at_target_means_no_adds():
    lay = cap_layers_to_position(_googl_layers(), held=200.0, target=200)
    assert lay["mode"] == "AT_TARGET" and lay["tranches"] == [] and lay["total_shares"] == 0
    assert "no adds" in layers_line(lay)
    assert "no adds" in layers_markdown(lay, "GOOGL")


def test_small_gap_fills_top_tranche_first():
    lay = cap_layers_to_position(_googl_layers(), held=198.6, target=200)
    assert lay["total_shares"] == 1 and len(lay["tranches"]) == 1 and lay["tranches"][0]["price"] == 268.31


def test_not_held_or_no_target_leaves_plan_unchanged():
    base = _googl_layers()
    assert cap_layers_to_position(base, held=0, target=200) == base
    assert cap_layers_to_position(base, held=None, target=None) == base
    held_no_target = cap_layers_to_position(base, held=50, target=None)
    assert held_no_target["total_shares"] == base["total_shares"]
    assert "no target" in held_no_target["position_note"]


# --- stop repair measured from the lowest fill (GOOGL 2026-10-29 gate rejection) ------------

def test_repair_widens_stop_below_zone_low_so_the_gate_passes():
    from src.logic import order_ticket

    sp = {"tactical_stop": 260.30, "target_1": 312.38, "entry_zone_low": 265.64, "entry_zone_high": 268.31}
    notes = order_ticket.repair_long_shares_plan(sp, close=282.82, atr=10.675, pine_stop=True)
    assert sp["tactical_stop"] == 258.17          # 265.64 - 0.7 * 10.675
    assert any("lowest fill" in n for n in notes)
    assert "repair_rejected" not in sp


def test_binding_block_carries_the_layered_line():
    from src.logic import report_consistency as rc

    lay = cap_layers_to_position(_googl_layers(), held=191.8084, target=200)
    wd = {"levels_source": "pine", "level_gate_rejected": True,
          "shares_plan": {"entry_zone_low": 265.64, "entry_zone_high": 268.31, "tactical_stop": 258.17,
                          "target_1": 312.38, "layers": lay}}
    blk = rc.binding_block(wd, [], {})
    assert "🪜 Layered entry: LAYERED 8: 3 @ 268.31 + 3 @ 266.98 + 2 @ 265.64" in blk
    assert "display only" in blk


def test_notional_cap_limits_total_shares_to_equity_pct():
    # spot 400, zone below, wide stop -> risk-budget math wants many shares, but 5% of a 100k
    # account at ~$400 top fill is 12 shares total (3 tranches x 4).
    lay = build_layers(400.0, 388.0, 392.0, 380.0, 460.0, atr=12.0, risk_budget_usd=500,
                       equity_usd=100_000)
    assert lay["size_cap"] == "notional"
    assert lay["total_shares"] <= int(100_000 * 0.05 / 392.0)  # top fill
    assert "equity" in lay["why"]


def test_earnings_gap_cap_binds_when_earnings_in_window():
    # earnings in 3 days, implied move 8%: a 2x gap on a $110 stock loses ~$17.6/share, so a
    # $1000 gap budget caps total shares near 56 regardless of the risk-budget count.
    lay = build_layers(110.0, 106.0, 108.0, 104.0, 125.0, atr=5.0, risk_budget_usd=500,
                       earnings_days=3, earnings_block_days=7, implied_move_pct=8.0, equity_usd=1_000_000)
    assert lay["size_cap"] == "earnings_gap"
    assert lay["total_shares"] <= int(1000 / (110.0 * 0.16)) + 2
    assert "earnings gap" in lay["why"]


def test_no_earnings_cap_outside_window():
    lay = build_layers(110.0, 106.0, 108.0, 104.0, 125.0, atr=5.0, risk_budget_usd=500,
                       earnings_days=40, implied_move_pct=8.0, equity_usd=1_000_000)
    assert lay["size_cap"] in (None, "notional")


def test_binding_block_tags_non_actionable_stalk_without_gate_flag():
    # The live NOW bug: a STALK verdict with pine levels, level_gate_rejected not set in the JSON,
    # LIMITS_ONLY layers. The layered line must still be tagged, not read as 'buy 85 now'.
    from src.logic import report_consistency as rc

    lay = build_layers(111.80, 110.33, 112.42, 103.86, 120.17, atr=8.36, risk_budget_usd=500,
                       equity_usd=1_000_000)
    assert lay["mode"] == "LIMITS_ONLY"
    wd = {"levels_source": "pine", "verdict": "STALK",
          "shares_plan": {"entry_zone_low": 110.33, "entry_zone_high": 112.42, "tactical_stop": 103.86,
                          "target_1": 120.17, "target_2": 136.54, "layers": lay}}
    blk = rc.binding_block(wd, [], {})
    assert "not actionable at market now" in blk


def test_binding_block_flags_invalidation_below_sizing_stop():
    from src.logic import report_consistency as rc

    wd = {"levels_source": "pine", "atr14": 8.36,
          "shares_plan": {"tactical_stop": 103.86, "target_1": 120.17},
          "invalidation": {"price_level": 99.5}}
    issues = rc.check("stop 103.86 T1 120.17", wd, {}, spot=111.80)
    assert any("invalidation" in i and "99.5" in i for i in issues)
