"""
Test script for fetch_prior_research_tool.
Verifies lookback across canonical reports/<date>/<ticker>_summary.md files.
"""
import sys
import re
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def fetch_prior_research_tool(ticker: str, lookback_days: int = 14, date_str: str = None) -> str:
    """
    Retrieves the most recent prior research summary and trade plan from 'reports/<date>/<ticker>_summary.md'
    within the last lookback_days prior to date_str.
    """
    reports_dir = BASE_DIR / "reports"
    if not reports_dir.exists():
        return f"No prior research found for {ticker} (reports/ directory does not exist)."
        
    date_cutoff = date_str or datetime.now().strftime("%Y-%m-%d")
    dates = sorted([d.name for d in reports_dir.glob("202*") if d.is_dir() and d.name < date_cutoff], reverse=True)
    
    for d in dates[:lookback_days]:
        p = reports_dir / d / f"{ticker}_summary.md"
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                header = lines[0] if lines else f"# {ticker} | {d}"
                
                # Extract TLDR section
                tldr_match = re.search(r"## ⚡ TLDR / EXECUTIVE SUMMARY\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
                tldr = tldr_match.group(1).strip() if tldr_match else ""
                
                # Extract Plan A section
                plan_a_match = re.search(r"### Plan A.*?\n(.*?)(?=\n###|\n##|\Z)", text, re.DOTALL)
                plan_a = plan_a_match.group(1).strip() if plan_a_match else ""
                
                # Compute days elapsed
                try:
                    d_obj = datetime.strptime(d, "%Y-%m-%d")
                    curr_obj = datetime.strptime(date_cutoff, "%Y-%m-%d")
                    days_ago = (curr_obj - d_obj).days
                except Exception:
                    days_ago = "N/A"
                    
                output = [
                    f"### PRIOR RESEARCH SUMMARY FOR {ticker} (From {d} — {days_ago} days ago)",
                    f"**File:** `reports/{d}/{ticker}_summary.md`",
                    f"**Header:** {header}",
                    "",
                    "#### Executive Summary & Verdict:",
                    tldr,
                ]
                if plan_a:
                    output.extend(["", "#### Prior Plan A Levels:", plan_a])
                    
                return "\n".join(output)
            except Exception as e:
                return f"Error reading prior report for {ticker} from {d}: {e}"
                
    return f"No prior research found for {ticker} in reports/ prior to {date_cutoff} (looked back {min(len(dates), lookback_days)} dates)."


if __name__ == "__main__":
    res = fetch_prior_research_tool("HOOD", date_str="2026-08-17")
    assert "PRIOR RESEARCH SUMMARY FOR HOOD" in res
    assert "2026-08-14" in res
    print("TEST PASSED: Found prior HOOD research from 2026-08-14.")
