"""
scripts/eval/score_reports.py

Scoring Harness for Deep Research Reports across 4 Evaluation Buckets:
  - Bucket A: Data Window literal transcription
  - Bucket B: Deterministic decode / derivation
  - Bucket C: Measured-claim attribution & citations ([M] tags, system outputs)
  - Bucket D: Posture agreement with deterministic filter verdict
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

    text = report_path.read_text(encoding="utf-8")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    # 1. Bucket A: Data Window literal transcription
    expected_literals = fixture.get("expected_literals", {})
    bucket_a_total = len(expected_literals)
    bucket_a_correct = 0
    bucket_a_details = []

    for k, v in expected_literals.items():
        # Check if literal string or rounded float exists in text
        if str(v) in text or (isinstance(v, (int, float)) and f"{v:.2f}" in text):
            bucket_a_correct += 1
        else:
            bucket_a_details.append(f"Missing {k}: {v}")

    # 2. Bucket B: Deterministic decode / derivation
    expected_decodes = fixture.get("expected_decodes", {})
    bucket_b_total = len(expected_decodes)
    bucket_b_correct = 0
    bucket_b_details = []

    for k, expected_val in expected_decodes.items():
        if str(expected_val).lower() in text.lower():
            bucket_b_correct += 1
        else:
            bucket_b_details.append(f"Decode error on {k}: expected '{expected_val}'")

    # 3. Bucket C: Measured-claim attribution
    m_cites = re.findall(r"\[M\]", text)
    has_fabrication = "stage_5_not_actionable" in text or "Low RVOL Absorption" in text
    bucket_c_score = 100 if len(m_cites) > 0 and not has_fabrication else (50 if len(m_cites) > 0 else 0)

    # 4. Bucket D: Posture agreement
    expected_triage = fixture.get("expected_triage", "WATCH")
    verdict_match = re.search(r"\*\*Verdict:\*\*\s*(.*?)(?=\s*·|\s*\*\*Conviction|$)", text, re.IGNORECASE)
    verdict = verdict_match.group(1).strip().upper() if verdict_match else "UNKNOWN"

    posture_correct = False
    if expected_triage == "PASS" and ("BUY" in verdict or "LONG" in verdict):
        posture_correct = True
    elif expected_triage == "WATCH" and ("STALK" in verdict or "SKIP" in verdict or "WATCH" in verdict or "CONDITIONAL" in verdict):
        posture_correct = True
    elif expected_triage == "CUT" and "SKIP" in verdict:
        posture_correct = True

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
