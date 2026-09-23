"""
Launcher script to run the Nightly Intraday Statistics Job.
Aggregates the shadow ledger in alert_db.db and refreshes skills/postmortem_learnings.md.
"""

import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from src.tracking.intraday_stats import regenerate_postmortem_markdown

if __name__ == "__main__":
    print("Running Nightly Intraday Stats Job...")
    out = regenerate_postmortem_markdown()
    if out:
        print("Successfully regenerated skills/postmortem_learnings.md with empirical metrics.")
    else:
        print("No intraday signals found in database to process.")
