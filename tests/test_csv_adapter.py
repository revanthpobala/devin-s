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


class TestCSVAdapter(unittest.TestCase):
    def test_csv_to_datawindow_basic(self):
        # Create a temporary CSV with 15 rows of synthetic data
        rows = []
        base_price = 100.0
        for i in range(15):
            price = base_price + i * 1.5
            rows.append({
                "time": f"2026-07-{i+1:02d}",
                "close": price,
                "buy score": 80.0,
                "sell score": 20.0,
                "ext%": 5.0,
            })
        df = pd.DataFrame(rows)

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "NVDA_datawindow.csv")
            json_path = os.path.join(tmpdir, "NVDA_datawindow.json")
            df.to_csv(csv_path, index=False)

            snapshot, hist_df, realvol_10d, ret_10d = csv_to_datawindow(csv_path, json_path)

            self.assertIn("close", snapshot)
            self.assertEqual(snapshot["close"], str(base_price + 14 * 1.5))
            self.assertTrue(os.path.exists(json_path))

            # Verify JSON readable
            with open(json_path, "r", encoding="utf-8") as f:
                saved_json = json.load(f)
            self.assertEqual(saved_json["close"], str(base_price + 14 * 1.5))

            # Verify ret_10d and realvol_10d calculated
            self.assertIsNotNone(ret_10d)
            self.assertIsNotNone(realvol_10d)
            self.assertGreater(ret_10d, 0.0)
            self.assertGreater(realvol_10d, 0.0)

    def test_csv_to_datawindow_prefixed_header(self):
        rows = []
        for i in range(12):
            rows.append({
                "time": f"2026-07-{i+1:02d}",
                "NASDAQ:AAPL, D: Close": 150.0 + i,
                "Buy Score": 75.0,
            })
        df = pd.DataFrame(rows)
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "AAPL.csv")
            json_path = os.path.join(tmpdir, "AAPL.json")
            df.to_csv(csv_path, index=False)
            snapshot, hist_df, realvol_10d, ret_10d = csv_to_datawindow(csv_path, json_path)
            self.assertIsNotNone(ret_10d)
            self.assertIsNotNone(realvol_10d)


if __name__ == "__main__":
    unittest.main()
