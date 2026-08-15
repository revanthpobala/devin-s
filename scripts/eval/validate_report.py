"""
scripts/eval/validate_report.py

Output Number Validator: Verifies that numeric claims in a generated Deep Research report
originate from ground-truth sources (Data Window, triage record, GEX, TV strategy finder, and news).
Orphaned/fabricated numbers (such as hallucinated moving averages or fake exclusions) are flagged.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


def extract_numbers_from_text(text: str) -> List[Dict[str, Any]]:
    """Extract numeric tokens (prices, percentages, floats, ints) from markdown text with line context."""
    results = []
    lines = text.split("\n")
    # Matches $123.45, 123.45%, +12.3%, -4.5%, 123.45, etc.
    pattern = re.compile(r"(?:\$|\b)([+-]?\d+(?:\.\d+)?%?)\b")

    for line_idx, line in enumerate(lines, 1):
        for match in pattern.finditer(line):
            val_str = match.group(1).replace("$", "").replace("%", "").strip()
            try:
                val = float(val_str)
                results.append({
                    "raw": match.group(0),
                    "value": val,
                    "line_number": line_idx,
                    "line_text": line.strip(),
                })
            except ValueError:
                continue
    return results


def collect_ground_truth_numbers(*sources: Any) -> Set[float]:
    """Collect all known numbers from Data Window, triage record, options/GEX, strategies, and text payloads."""
    known = set()

    def _add_recursive(item: Any):
        if isinstance(item, (int, float)):
            known.add(round(float(item), 2))
            known.add(round(float(item), 4))
        elif isinstance(item, str):
            for m in re.finditer(r"[-+]?\d+(?:\.\d+)?", item):
                try:
                    val = float(m.group(0))
                    known.add(round(val, 2))
                    known.add(round(val, 4))
                except ValueError:
                    pass
        elif isinstance(item, dict):
            for v in item.values():
                _add_recursive(v)
        elif isinstance(item, list):
            for v in item:
                _add_recursive(v)

    for src in sources:
        if src:
            _add_recursive(src)
    return known


def validate_report(report_path: Path, datawindow_path: Path, output_json: Path) -> Dict[str, Any]:
    if not report_path.exists():
        raise FileNotFoundError(f"Report not found: {report_path}")

    report_text = report_path.read_text(encoding="utf-8")
    dw_data = json.loads(datawindow_path.read_text(encoding="utf-8")) if datawindow_path.exists() else {}

    # Discover and load companion ground-truth artifacts
    ticker = datawindow_path.name.split("_")[0]
    data_dir = datawindow_path.parent

    triage_p = data_dir / f"{ticker}_triage.json"
    triage_data = json.loads(triage_p.read_text(encoding="utf-8")) if triage_p.exists() else {}

    gex_p = data_dir / f"{ticker}_gex.json"
    gex_data = json.loads(gex_p.read_text(encoding="utf-8")) if gex_p.exists() else {}

    tv_strat_p = data_dir / f"{ticker}_tv_strategies.json"
    tv_strat_data = json.loads(tv_strat_p.read_text(encoding="utf-8")) if tv_strat_p.exists() else []

    news_p = data_dir / f"{ticker}_news_research.md"
    news_text = news_p.read_text(encoding="utf-8") if news_p.exists() else ""

    extracted = extract_numbers_from_text(report_text)
    ground_truth_numbers = collect_ground_truth_numbers(dw_data, triage_data, gex_data, tv_strat_data, news_text)

    # Check for known fabricated phrases
    fabricated_phrases = []
    if "stage_5_not_actionable" in report_text:
        fabricated_phrases.append("stage_5_not_actionable (fabricated hard exclusion)")

    orphans = []
    # Standard numbers exempt from orphan checking (common counts, headers, standard calendar years/DTEs)
    EXEMPT_NUMBERS = {
        0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0,
        16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 25.0, 30.0, 45.0, 50.0, 60.0, 90.0, 100.0, 120.0,
        150.0, 180.0, 200.0, 250.0, 365.0, 2024.0, 2025.0, 2026.0, 2027.0
    }

    for item in extracted:
        v = round(item["value"], 2)
        if v in EXEMPT_NUMBERS:
            continue
        if v not in ground_truth_numbers and round(v, 1) not in ground_truth_numbers:
            # Check if within 0.5% tolerance of any ground truth number (for minor rounding/averaging)
            matched = any(abs(v - gt) <= max(0.02, abs(gt) * 0.005) for gt in ground_truth_numbers)
            if not matched:
                orphans.append(item)

    summary = {
        "report": str(report_path),
        "total_numbers_checked": len(extracted),
        "orphans_count": len(orphans),
        "fabricated_phrases": fabricated_phrases,
        "orphans": orphans[:50],  # cap list
        "is_clean": len(orphans) == 0 and len(fabricated_phrases) == 0,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python validate_report.py <report.md> <datawindow.json> [output_validation.json]")
        sys.exit(1)

    rep_p = Path(sys.argv[1])
    dw_p = Path(sys.argv[2])
    out_p = Path(sys.argv[3]) if len(sys.argv) > 3 else rep_p.with_name(f"{rep_p.stem}_validation.json")

    res = validate_report(rep_p, dw_p, out_p)
    print(f"Validation finished for {rep_p.name}: Clean={res['is_clean']} (Orphans={res['orphans_count']}, Fabricated Phrases={len(res['fabricated_phrases'])})")
