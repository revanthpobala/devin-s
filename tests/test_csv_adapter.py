"""
tests/test_csv_adapter.py

Unit tests for src/data/csv_adapter.py.
"""

import json
import os
import tempfile
import unittest

import pandas as pd
from src.data.csv_adapter import csv_to_datawindow


def _make_dummy_df(nrows=280):
    rows = []
    base_price = 100.0
    for i in range(nrows):
        day = (i % 28) + 1
        month = (i // 28) + 1
        rows.append({
            "time": f"2026-{month:02d}-{day:02d}",
            "open": base_price + i * 1.0,
            "high": base_price + i * 2.0,
            "low": base_price + i * 0.5,
            "close": base_price + i * 1.5,
            "buy score": 80.0,
            "sell score": 20.0,
            "ext%": 5.0,
            "hv20 (ann %)": 25.0,
            "stage (1=": 2,
            **{f"col_{k}": 0.0 for k in range(52)},
        })
    return pd.DataFrame(rows)


from unittest.mock import patch

class TestCSVAdapter(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("src.plugins.plugin_manager.PluginManager.run_all", return_value={})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_csv_to_datawindow_basic(self):
        df = _make_dummy_df(nrows=280)
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "NVDA_datawindow.csv")
            json_path = os.path.join(tmpdir, "NVDA_datawindow.json")
            df.to_csv(csv_path, index=False)

            snapshot, hist_df, realvol_10d, ret_10d = csv_to_datawindow(csv_path, json_path)

            self.assertIn("close", snapshot)
            self.assertEqual(snapshot["close"], str(100.0 + 279 * 1.5))
            self.assertIn("bar_date", snapshot)
            self.assertEqual(snapshot.get("ticker"), "NVDA")
            self.assertTrue(os.path.exists(json_path))

            # Verify JSON readable
            with open(json_path, "r", encoding="utf-8") as f:
                saved_json = json.load(f)
            self.assertEqual(saved_json["close"], str(100.0 + 279 * 1.5))
            self.assertEqual(saved_json.get("ticker"), "NVDA")

            # Verify ret_10d and realvol_10d calculated
            self.assertIsNotNone(ret_10d)
            self.assertIsNotNone(realvol_10d)
            self.assertGreater(ret_10d, 0.0)
            self.assertGreater(realvol_10d, 0.0)

    def test_csv_to_datawindow_prefixed_header(self):
        df = _make_dummy_df(nrows=280)
        df.rename(columns={"close": "NASDAQ:AAPL, D: Close"}, inplace=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "AAPL.csv")
            json_path = os.path.join(tmpdir, "AAPL.json")
            df.to_csv(csv_path, index=False)
            snapshot, hist_df, realvol_10d, ret_10d = csv_to_datawindow(csv_path, json_path)
            self.assertEqual(snapshot.get("ticker"), "AAPL")
            self.assertIsNotNone(ret_10d)
            self.assertIsNotNone(realvol_10d)

    def test_csv_to_datawindow_explicit_ticker(self):
        df = _make_dummy_df(nrows=280)
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "data.csv")
            json_path = os.path.join(tmpdir, "data.json")
            df.to_csv(csv_path, index=False)
            snapshot, hist_df, realvol_10d, ret_10d = csv_to_datawindow(
                csv_path, json_path, ticker="TSLA"
            )
            self.assertEqual(snapshot.get("ticker"), "TSLA")

    def test_format_datawindow_val_precision(self):
        from src.data.csv_adapter import _format_datawindow_val

        # Integer formatting
        self.assertEqual(_format_datawindow_val("0.0"), "0")
        self.assertEqual(_format_datawindow_val(4.0), "4")
        self.assertEqual(_format_datawindow_val("10"), "10")

        # Full precision float preservation
        self.assertEqual(_format_datawindow_val("233.702913"), "233.702913")
        self.assertEqual(_format_datawindow_val(233.702913), "233.702913")

    def test_protocol_2_and_rsi2_decoding(self):
        from src.data.csv_adapter import decode_and_enrich_datawindow

        # Events pack 2051 = setupEvent(1) + armedEvent(2) + pending stateCode(2 * 1024 = 2048)
        # Stage age pack 18 = weinsteinStage(2) + stageAgeBars(2 * 8 = 16)
        snapshot = {
            "RSI2 Protocol Version": 2,
            "RSI2 Events Pack": 2051,
            "Context Stage Age Pack": 18,
            "Energy IV30 Ann Pct": 45.0,
            "_realvol_60d": 30.0,
        }
        df = _make_dummy_df(nrows=280)
        enriched = decode_and_enrich_datawindow(snapshot, df, ticker="TEST")

        self.assertEqual(enriched.get("_protocol_version"), 2)
        self.assertEqual(enriched.get("_rsi2_events_pack_raw"), 2051)
        self.assertTrue(enriched.get("_rsi2_setup_event"))
        self.assertTrue(enriched.get("_rsi2_armed_event"))
        self.assertEqual(enriched.get("_rsi2_state_code"), 2)
        self.assertEqual(enriched.get("_rsi2_state_name"), "PENDING")

        self.assertEqual(enriched.get("_weinstein_stage"), 2)
        self.assertEqual(enriched.get("_stage_age_bars"), 2)

        self.assertIn("Synthetic Volatility Proxy", enriched.get("_options_vol_regime", ""))

    def test_protocol_2_data_window_filter_pass(self):
        from src.logic.data_window_filter import run_data_window_filter

        # Protocol 2 snapshot with RSI2 setup and missing legacy stage & long stop loss
        raw = {
            "time": "2026-09-18",
            "close": "250.00",
            "open": "249.00",
            "high": "252.00",
            "low": "248.00",
            "ma 20 fast": "245.00",
            "ma 50 mid": "240.00",
            "ma 200 slow": "220.00",
            "weinstein ma 150": "235.00",
            "long setup score": "85.0",
            "short pressure score": "15.0",
            "context stage age pack": "18",  # Weinstein stage 2, age 2
            "evidence bias pct above 50 bull": "65.0",
            "regime 0 hlt 1 ext 2 clmx 3 dist 4 dn 5 ign 6 sqz": "0",
            "ext pct vs ma200": "13.6",
            "exhaustion gradient": "0.1",
            "long rev zone": "0",
            "short rev zone": "0",
            "long entry zone bot": "245.0",
            "long entry zone top": "248.0",
            "rsi2 protocol version": "2",
            "rsi2 events pack": "2051",
            "rsi2 entry or opening ceiling": "251.50",
            "rsi2 fixed stop": "233.702913",
            "rsi2 fixed target": "282.594174",
        }

        verdict = run_data_window_filter("AMZN", raw)

        self.assertFalse(verdict.get("bad_data"))
        self.assertEqual(verdict.get("triage"), "PASS")
        self.assertEqual(verdict.get("mode"), "RSI2_LONG")
        self.assertEqual(verdict.get("reason"), "rsi2_setup_lane")
        self.assertEqual(verdict.get("protocol_version"), 2)
        self.assertEqual(verdict.get("rsi2_events_pack"), 2051)
        self.assertTrue(verdict.get("rsi2_setup_event"))
        # Verify stop and target were mapped into the plan
        self.assertEqual(verdict["long_plan"]["stop"], 233.702913)
        self.assertEqual(verdict["long_plan"]["target"], 282.594174)


if __name__ == "__main__":
    unittest.main()

