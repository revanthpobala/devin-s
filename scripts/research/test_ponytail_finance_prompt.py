import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import BASE_DIR

class TestPonytailFinancePrompt(unittest.TestCase):
    def test_ponytail_finance_file_exists(self):
        gem_path = BASE_DIR / "gems" / "ponytail_finance.md"
        rule_path = BASE_DIR / ".agents" / "rules" / "ponytail_finance.md"
        self.assertTrue(gem_path.exists(), "gems/ponytail_finance.md must exist")
        self.assertTrue(rule_path.exists(), ".agents/rules/ponytail_finance.md must exist")

        content = gem_path.read_text(encoding="utf-8")
        self.assertIn("DOES A REAL MEASURED EDGE EXIST?", content)
        self.assertIn("SHORTEST PATH VEHICLE", content)
        self.assertIn("HARD DEFENSIVE ANCHOR FIRST", content)

    def test_deep_research_injects_ponytail(self):
        deep_research_path = BASE_DIR / "src" / "logic" / "deep_research.py"
        code = deep_research_path.read_text(encoding="utf-8")
        self.assertIn("ponytail_finance.md", code)
        self.assertIn("--- PONYTAIL FINANCE (Occam's Razor & PM Discipline) ---", code)

if __name__ == "__main__":
    unittest.main()
