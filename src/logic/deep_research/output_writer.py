"""output_writer.py — clean, validate, and write Pass 2 report files."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ReportMetrics:
    verdict: str = "N/A"
    conviction: str = "N/A"
    thesis: str = ""
    entry: str = ""
    stop: str = ""
    target: str = ""


def _strip_tool_artifacts(text: str) -> str:
    """Remove leftover tool_call / function XML fragments from LLM output."""
    text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<function=.*?</function>', '', text, flags=re.DOTALL)
    return text.strip()


def _trim_preamble(text: str) -> str:
    """Strip any prose before the first markdown heading."""
    match = re.search(r"(?m)^#+\s+.*", text)
    if match and match.start() > 0:
        return text[match.start():].strip()
    return text


def _ensure_h1(text: str, ticker: str, label: str) -> str:
    if not re.search(r"(?m)^#\s+", text):
        return f"# {ticker} | {label}\n\n" + text
    return text


def extract_metrics(clean_response: str) -> ReportMetrics:
    """Extract provisional metrics from a markdown report via regex."""
    m = ReportMetrics()

    verdict_match = re.search(
        r"\*\*Verdict:\*\*\s*(.*?)(?=\s*·|\s*\*\*Conviction|$)", clean_response, re.IGNORECASE
    ) or re.search(
        r"\|\s*[*]*Equity[^\*|]*[*]*\s*\|\s*[*]*([A-Z\s\(\)]+?)[*]*\s*\|", clean_response, re.IGNORECASE
    ) or re.search(
        r"\|\s*[*]*Options[^\*|]*[*]*\s*\|\s*[*]*([A-Z\s\(\)]+?)[*]*\s*\|", clean_response, re.IGNORECASE
    )
    if verdict_match:
        m.verdict = verdict_match.group(1).strip()

    conviction_match = re.search(
        r"\*\*Conviction[^\*]*:\*\*\s*([\d\.]+)(?:\s*/\s*10)?", clean_response, re.IGNORECASE
    ) or re.search(r"\|\s*([\d\.]+)\s*/\s*10\s*\|", clean_response, re.IGNORECASE)
    if conviction_match:
        m.conviction = conviction_match.group(1).strip()

    thesis_match = re.search(r"\*\*The Thesis in 2 Sentences:\*\*\s*(.+)", clean_response, re.IGNORECASE)
    if thesis_match:
        m.thesis = thesis_match.group(1).strip()

    # Matches: | Label | $xxx |  OR  - **Label:** $xxx  OR  - Label: $xxx
    entry_match = re.search(
        r"(?:"
        r"\|\s*(?:\*{1,2})?(?:Pullback\s+)?(?:Limit\s+)?Entry(?:\*{1,2})?\s*\|\s*[$]?\s*(\d+(?:\.\d+)?)"
        r"|-\s+(?:\*{1,2})?(?:Pullback\s+)?(?:Limit\s+)?Entry(?:\*{1,2})?:\*{0,2}\s*[$]?\s*(\d+(?:\.\d+)?)"
        r")",
        clean_response, re.IGNORECASE,
    )
    if entry_match:
        m.entry = (entry_match.group(1) or entry_match.group(2)) or ""

    stop_match = re.search(
        r"(?:"
        r"\|\s*(?:\*{1,2})?(?:Tactical\s+)?Stop(?:\s*Loss)?(?:\*{1,2})?\s*\|\s*[$]?\s*(\d+(?:\.\d+)?)"
        r"|-\s+(?:\*{1,2})?(?:Tactical\s+)?Stop(?:\s*Loss)?(?:\*{1,2})?:\*{0,2}\s*[$]?\s*(\d+(?:\.\d+)?)"
        r")",
        clean_response, re.IGNORECASE,
    )
    if stop_match:
        m.stop = (stop_match.group(1) or stop_match.group(2)) or ""

    target_match = re.search(
        r"(?:"
        r"\|\s*(?:\*{1,2})?Target(?:\s*1)?(?:\*{1,2})?\s*\|\s*[$]?\s*(\d+(?:\.\d+)?)"
        r"|-\s+(?:\*{1,2})?Target(?:\s*1)?(?:\*{1,2})?:\*{0,2}\s*[$]?\s*(\d+(?:\.\d+)?)"
        r")",
        clean_response, re.IGNORECASE,
    )
    if target_match:
        m.target = (target_match.group(1) or target_match.group(2)) or ""

    return m


def save_model_a_report(
    ticker: str,
    date_str: str,
    response: str,
    out_dir: Path,
    out_path: Path,
    reports_dir: Path,
    drift_checker,
) -> tuple[str, ReportMetrics]:
    """Clean, validate and persist Model A output. Returns (clean_text, metrics)."""
    if not response:
        logger.error(f"[{ticker}] Model A (Summary) returned empty response.")
        return "", ReportMetrics()

    out_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    text = _strip_tool_artifacts(response)
    text = _trim_preamble(text)

    if len(text) < 200:
        logger.warning(f"[{ticker}] Model A response too short (len={len(text)}). Rejecting.")
        return "", ReportMetrics()

    text = _ensure_h1(text, ticker, "RESEARCH DOSSIER & THESIS")

    out_path.write_text(text, encoding="utf-8")

    try:
        drift_checker.check_and_write(ticker, date_str, text, out_dir)
    except Exception as e:
        logger.warning(f"[{ticker}] Thesis drift check failed: {e}")

    digest_path = reports_dir / f"{ticker}_summary.md"
    digest_path.write_text(text, encoding="utf-8")

    metrics = extract_metrics(text)
    logger.info(f"[{ticker}] Model A Summary generated successfully.")
    return text, metrics


def save_model_b_report(
    ticker: str,
    date_str: str,
    ind_response: str,
    out_dir: Path,
    reports_dir: Path,
) -> str:
    """Clean, validate and persist Model B (independent) output. Returns clean text."""
    if not ind_response:
        logger.error(f"[{ticker}] Model B (Independent) returned empty response.")
        return ""

    text = _strip_tool_artifacts(ind_response)
    text = _trim_preamble(text)

    if len(text) < 200:
        logger.warning(f"[{ticker}] Model B response too short (len={len(text)}). Rejecting.")
        return ""

    text = _ensure_h1(text, ticker, "INDEPENDENT QUANTITATIVE & MACRO THESIS")

    ind_report_path = reports_dir / f"{ticker}_independent.md"
    ind_report_path.write_text(text, encoding="utf-8")

    ind_triage_path = out_dir / f"{ticker}_independent_thesis.md"
    ind_triage_path.write_text(text, encoding="utf-8")

    logger.info(f"[{ticker}] Independent Macro & Technical Summary generated at {ind_report_path}!")
    return text
