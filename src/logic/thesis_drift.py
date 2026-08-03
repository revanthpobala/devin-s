import glob
import json
import logging
import re
from pathlib import Path
from typing import Optional

from src import config
from src.clients.llm_client import query_local_llm

logger = logging.getLogger(__name__)

DRIFT_PROMPT = """You are comparing two INDEPENDENTLY-GENERATED research theses for the same ticker, written on different dates. Identify:
1. FACTUAL CONTRADICTIONS: things stated as fact that conflict (dates, price targets, EPS figures, analyst actions, downgrade/upgrade dates). For each, quote both versions + dates.
2. SETUP CHANGES: genuine, expected day-over-day differences (verdict, conviction, zone/stop/target levels, price action) — these are NOT errors, just report them for context.
Output ONLY JSON: {"contradictions": [...], "setup_changes": [...]}"""


class ThesisDriftChecker:
    """Post-hoc, LOCAL-only (free) consistency check between today's finished Minimax
    thesis and the most recent prior one for the same ticker. Runs strictly AFTER the
    paid pass writes its thesis — never feeds anything back into Minimax's own prompt,
    so the paid research stays fully blind/independent. No-op (near-zero cost) for
    tickers with no prior thesis on record."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or config.BASE_DIR

    def find_prior_thesis(self, ticker: str, today_str: str) -> Optional[tuple[str, str]]:
        """Most recent {ticker}_gemini_thesis.md across all triage/raw date folders,
        excluding today. Returns (date_str, text) or None."""
        patterns = [
            str(self.base_dir / "data" / "triage" / "*" / "_DEEP_RESEARCH" / f"{ticker}_gemini_thesis.md"),
            str(self.base_dir / "data" / "triage" / "*" / "force" / f"{ticker}_gemini_thesis.md"),
            str(self.base_dir / "data" / "raw" / "*" / f"{ticker}_gemini_thesis.md"),
        ]
        candidates = []
        for p in patterns:
            candidates.extend(glob.glob(p))
        candidates.sort(reverse=True)

        for path in candidates:
            match = re.search(r"20\d{2}-\d{2}-\d{2}", path)
            date_part = match.group(0) if match else Path(path).parts[-3]
            if date_part == today_str:
                continue
            try:
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
                return date_part, text
            except Exception as e:
                logger.warning(f"[{ticker}] failed to read prior thesis {path}: {e}")
        return None

    def check(self, ticker: str, today_str: str, today_thesis_text: str) -> Optional[dict]:
        prior = self.find_prior_thesis(ticker, today_str)
        if not prior:
            return None
        prior_date, prior_text = prior
        # Truncate massive theses so combined prompt fits local LLM 8k context window (~3.5k tokens max)
        prior_snippet = prior_text[:7000] if len(prior_text) > 7000 else prior_text
        today_snippet = today_thesis_text[:7000] if len(today_thesis_text) > 7000 else today_thesis_text

        user_prompt = (
            f"PRIOR THESIS ({prior_date}):\n{prior_snippet}\n\n---\n\n"
            f"TODAY'S THESIS ({today_str}):\n{today_snippet}"
        )
        raw = query_local_llm(
            DRIFT_PROMPT,
            user_prompt,
            json_mode=True,
            max_tokens=1024,
            disable_thinking=True,
        )
        if not raw:
            return None

        # Clean markdown code fences and think tags if present
        clean_raw = str(raw).strip()
        if "</think>" in clean_raw:
            clean_raw = clean_raw.split("</think>")[-1].strip()
        if "```" in clean_raw:
            lines = [line for line in clean_raw.splitlines() if not line.strip().startswith("```")]
            clean_raw = "\n".join(lines).strip()

        try:
            result = json.loads(clean_raw)
            if isinstance(result, dict):
                result["prior_date"] = prior_date
                return result
        except Exception as e:
            logger.warning(f"[{ticker}] drift check JSON parse failed: {e}")
            return None
        return None

    def check_and_write(
        self, ticker: str, today_str: str, today_thesis_text: str, deep_dir: Path
    ) -> Optional[dict]:
        result = self.check(ticker, today_str, today_thesis_text)
        if result:
            out_path = deep_dir / f"{ticker}_drift_check.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            logger.info(f"[{ticker}] drift check written -> {out_path}")
        return result
