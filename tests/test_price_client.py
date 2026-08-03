from unittest.mock import patch

import src.clients.price_client as pc


def test_get_current_price_empty_symbol():
    assert pc.get_current_price("") is None
    assert pc.get_current_price("   ") is None


def test_get_current_price_mocked():
    with patch.object(pc, "get_current_price", return_value=123.45):
        price = pc.get_current_price("AAPL")
        assert price == 123.45
