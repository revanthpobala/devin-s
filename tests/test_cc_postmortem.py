from src.tracking import cc_postmortem as cc


def _order(oid, inst, sym, px, qty, day):
    return {"orderId": oid, "status": "FILLED", "closeTime": f"{day}T15:00:00+0000",
            "orderLegCollection": [{"legId": 1, "instruction": inst, "quantity": qty,
                                    "instrument": {"assetType": "OPTION", "symbol": sym, "underlyingSymbol": "AMZN"}}],
            "orderActivityCollection": [{"executionLegs": [{"legId": 1, "price": px, "quantity": qty, "time": f"{day}T15:00:00+0000"}]}]}


SYM = "AMZN  261016C00270000"


def test_parse_and_fifo_buyback_then_expiry():
    p = cc.parse_orders([_order(1, "SELL_TO_OPEN", SYM, 3.0, 2, "2026-10-01"),
                         _order(2, "BUY_TO_CLOSE", SYM, 1.0, 1, "2026-10-08"),
                         {"orderId": 3, "status": "CANCELED"}])
    assert p["opens"][0]["strike"] == 270.0 and p["opens"][0]["expiry"] == "2026-10-16" and len(p["closes"]) == 1
    lots = [{**p["opens"][0], "open_date": "2026-10-01", "premium": 3.0}]
    cc.allocate_closes(lots, p["closes"])
    assert lots[0]["btc_qty"] == 1 and lots[0]["btc_cost"] == 1.0
    assert cc.settle_lot(lots[0], None, "2026-10-10") is None
    away = cc.settle_lot(lots[0], 280.0, "2026-10-17")
    assert away == {"outcome": "CALLED_AWAY", "leg_pnl": 3.0 - (1.0 + 10.0 * 1) / 2}
    assert cc.settle_lot(lots[0], 260.0, "2026-10-17")["outcome"] == "EXPIRED_OTM"


def test_full_buyback_settles_immediately():
    lot = {"qty": 1, "btc_qty": 1, "btc_cost": 1.5, "premium": 3.0, "strike": 270.0, "expiry": "2026-12-18"}
    assert cc.settle_lot(lot, None, "2026-10-10") == {"outcome": "BOUGHT_BACK", "leg_pnl": 1.5}
