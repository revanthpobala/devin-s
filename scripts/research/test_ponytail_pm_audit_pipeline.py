import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.logic.deep_research import run_ponytail_pm_review

class TestPonytailPMAuditPipeline(unittest.TestCase):
    @patch("src.logic.deep_research.query_local_llm")
    def test_run_ponytail_pm_review_mock(self, mock_query):
        mock_query.return_value = "### 1. R:R & Entry Audit\n- R:R is 0.80:1. Mandate STALK at $300.57.\n### 2. Structure & Downside Risk Audit\n- 4-leg Iron Condor is superior for Darvas box coiling.\n### 3. The ONE Thing Invalidation\n- Hold $300 Put Wall through FOMC."

        dw_dict = {
            "close": 306.72,
            "VP POC": 311.54,
            "Darvas Box Top": 316.02,
            "Darvas Box Bottom": 300.0,
            "Buy Score": 62.73,
            "Sell Score": 86.07,
            "IV Rank Pct": 89.7,
        }

        review = run_ponytail_pm_review(
            ticker="AAPL",
            date_str="2026-08-18",
            draft_thesis="# AAPL Draft Thesis\nBuy at $306.72 with $295 stop.",
            dw_dict=dw_dict,
        )

        self.assertIn("R:R & Entry Audit", review)
        self.assertIn("Iron Condor", review)
        self.assertIn("ONE Thing Invalidation", review)

if __name__ == "__main__":
    unittest.main()
