"""
scripts/eval/score_reports.py

Scoring Harness for Deep Research Reports across 4 Evaluation Buckets:
  - Bucket A: Data Window literal transcription (with numeric tolerance)
  - Bucket B: Deterministic decode / derivation (anchored regexes, no substring collision)
  - Bucket C: Measured-claim attribution & citations ([M] tags, system outputs)
  - Bucket D: Posture agreement with deterministic filter verdict (strict negative checks)
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


def score_report(report_path: Path, fixture_path: Path) -> Dict[str, Any]:
    if not report_path.exists():
        raise FileNotFoundError(f"Report not found: {report_path}")
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")

    raw_text = report_path.read_text(encoding="utf-8")
    # Normalize unicode minuses and dashes to standard ASCII minus
    text = raw_text.replace("−", "-").replace("–", "-").replace("—", "-")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Extract all floating point numbers in text for Bucket A tolerance checking
    extracted_nums = [float(m.group(0)) for m in re.finditer(r"[-+]?\d+(?:\.\d+)?", text)]

    # 1. Bucket A: Data Window literal transcription with tolerance & full precision
    expected_literals = fixture.get("expected_literals", {})
    bucket_a_total = len(expected_literals)
    bucket_a_correct = 0
    bucket_a_details = []

    for k, v in expected_literals.items():
        matched = False
        if str(v) in text:
            matched = True
        elif isinstance(v, (int, float)):
            # Check 2dp string and float proximity
            if f"{v:.2f}" in text or f"{v:.1f}" in text:
                matched = True
            else:
                matched = any(abs(num - v) <= max(0.02, abs(v) * 0.005) for num in extracted_nums)

        if matched:
            bucket_a_correct += 1
        else:
            bucket_a_details.append(f"Missing {k}: {v}")

    # 2. Bucket B: Deterministic decode / derivation with anchored regexes
    expected_decodes = fixture.get("expected_decodes", {})
    bucket_b_total = len(expected_decodes)
    bucket_b_correct = 0
    bucket_b_details = []

    for k, expected_val in expected_decodes.items():
        matched = False
        val_str = str(expected_val).upper()
        if k == "fade_gate":
            if val_str == "OFF":
                matched = bool(re.search(r"fade\s*gate[^\n\.\,]{0,40}\b(?:off|not active|inactive)\b", text, re.IGNORECASE))
            elif val_str == "ACTIVE":
                matched = bool(re.search(r"fade\s*gate[^\n\.\,]{0,40}\b(?:(?<!not )active|do not chase)\b", text, re.IGNORECASE))
        elif k == "zone_position":
            if val_str == "ZONELESS":
                matched = bool(re.search(r"\b(?:zoneless|no surviving entry zone|bounds are blank|blank)\b", text, re.IGNORECASE))
            else:
                matched = bool(re.search(rf"\b{re.escape(str(expected_val))}\b", text, re.IGNORECASE))
        else:
            matched = bool(re.search(rf"\b{re.escape(str(expected_val))}\b", text, re.IGNORECASE))

        if matched:
            bucket_b_correct += 1
        else:
            bucket_b_details.append(f"Decode error on {k}: expected '{expected_val}'")

    # 3. Bucket C: Measured-claim attribution
    m_cites = re.findall(r"\[M\]", text)
    has_fabrication = "stage_5_not_actionable" in text or "Low RVOL Absorption" in text
    bucket_c_score = 100 if len(m_cites) > 0 and not has_fabrication else (50 if len(m_cites) > 0 else 0)

    # 4. Bucket D: Posture agreement with strict negative checks
    expected_triage = fixture.get("expected_triage", "WATCH").upper()
    verdict_match = re.search(r"\*\*Verdict:\*\*\s*(.*?)(?=\s*·|\s*\*\*Conviction|$)", text, re.IGNORECASE)
    verdict = verdict_match.group(1).strip().upper() if verdict_match else "UNKNOWN"

    posture_correct = False
    if expected_triage == "PASS":
        posture_correct = bool(re.search(r"\b(?:BUY|LONG)\b", verdict, re.IGNORECASE))
    elif expected_triage == "WATCH":
        # Negative check: on non-PASS, fail if the verdict contains direct BUY / LEAN LONG / ENTER NOW
        has_violation = bool(re.search(r"\b(?:BUY|LEAN\s+LONG|ENTER\s+NOW)\b", verdict, re.IGNORECASE))
        has_permitted = bool(re.search(r"\b(?:STALK|SKIP|WATCH|CONDITIONAL|OPTIONS\s+CREDIT)\b", verdict, re.IGNORECASE))
        posture_correct = has_permitted and not has_violation
    elif expected_triage == "CUT":
        has_violation = bool(re.search(r"\b(?:BUY|LONG|LEAN|STALK)\b", verdict, re.IGNORECASE))
        posture_correct = bool(re.search(r"\bSKIP\b", verdict, re.IGNORECASE)) and not has_violation

    return {
        "report": report_path.name,
        "bucket_a": {
            "score": f"{bucket_a_correct}/{bucket_a_total}" if bucket_a_total > 0 else "N/A",
            "pct": round(bucket_a_correct / bucket_a_total * 100, 1) if bucket_a_total > 0 else 100.0,
            "defects": bucket_a_details,
        },
        "bucket_b": {
            "score": f"{bucket_b_correct}/{bucket_b_total}" if bucket_b_total > 0 else "N/A",
            "pct": round(bucket_b_correct / bucket_b_total * 100, 1) if bucket_b_total > 0 else 100.0,
            "defects": bucket_b_details,
        },
        "bucket_c": {
            "score_pct": bucket_c_score,
            "m_cites_count": len(m_cites),
            "has_fabrication": has_fabrication,
        },
        "bucket_d": {
            "expected_triage": expected_triage,
            "actual_verdict": verdict,
            "agreed": posture_correct,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python score_reports.py <report.md> <fixture.json>")
        sys.exit(1)

    res = score_report(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps(res, indent=2))
