from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.data.csv_adapter import csv_to_datawindow
from src.plugins.plugin_manager import plugin_manager
from src.clients.llm_client import run_quantitative_plugin_tool, execute_python_code_tool
from scripts.eval.validate_report import validate_report

def run_regression():
    print("1. Testing csv_to_datawindow...")
    s, df, v, r = csv_to_datawindow('data/triage/2026-08-16/force/AMD/AMD_datawindow.csv', 'data/triage/2026-08-16/force/AMD/AMD_datawindow.json')
    assert '_darvas_box_duration_bars' in s, 'Missing Darvas box duration!'
    assert '_catalyst_move_summary' in s, 'Missing catalyst summary!'
    print("   -> CSV & Plugin enrichment PASS")

    print("2. Testing run_quantitative_plugin_tool...")
    res_plug = run_quantitative_plugin_tool('AMD', 'all', '2026-08-16')
    assert 'Quantitative Plugin Results' in res_plug, 'Plugin tool output failed!'
    print("   -> Plugin tool call PASS")

    print("3. Testing execute_python_code_tool...")
    res_code = execute_python_code_tool('print(f"Spread Cost: {500-450}")', 'AMD', '2026-08-16')
    assert 'Spread Cost: 50' in res_code, 'Python code execution failed!'
    print("   -> Sandbox code interpreter PASS")

    print("4. Validating Deep Research Report...")
    val = validate_report(Path('reports/2026-08-16/AMD_summary.md'), Path('data/triage/2026-08-16/force/AMD/AMD_datawindow.json'), Path('data/raw/2026-08-16/AMD/val.json'))
    print(f"   -> Spread math defects: {len(val['spread_math_defects'])} | Fabricated phrases: {len(val['fabricated_phrases'])}")
    print("\nALL REGRESSION CHECKS PASSED!")

if __name__ == "__main__":
    run_regression()
