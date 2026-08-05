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


class TestCSVAdapter(unittest.TestCase):
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
            self.assertTrue(os.path.exists(json_path))

            # Verify JSON readable
            with open(json_path, "r", encoding="utf-8") as f:
                saved_json = json.load(f)
            self.assertEqual(saved_json["close"], str(100.0 + 279 * 1.5))

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
            self.assertIsNotNone(ret_10d)
            self.assertIsNotNone(realvol_10d)


if __name__ == "__main__":
    unittest.main()
