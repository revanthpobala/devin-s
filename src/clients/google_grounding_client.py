import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from src import config
from src.clients.search_client import is_junk_url

logger = logging.getLogger(__name__)

# Configurable model (defaults to gemini-3.5-flash-lite, overridable via GEMINI_GROUNDING_MODEL)
DEFAULT_GROUNDING_MODEL = os.getenv("GEMINI_GROUNDING_MODEL", "gemini-3.5-flash-lite")

# Free tier: 5,000 grounded queries/month (Gemini 3 family) — generous relative to
# our ~10-12 finalists/day * 1-2 questions/day usage. Track anyway so an unexpected
# spike doesn't silently roll into paid overage ($14/1,000 queries).
USAGE_FILE = config.BASE_DIR / "data" / "gemini_grounding_usage.json"
MONTHLY_FREE_CAP = int(os.getenv("GEMINI_GROUNDING_MONTHLY_CAP", "5000"))


def _check_and_increment_quota() -> bool:
    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")
    usage: dict[str, Any] = {}
    if USAGE_FILE.exists():
        try:
            usage = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            usage = {}
    if usage.get("month") != month_key:
        usage = {"month": month_key, "count": 0}
    try:
        count = int(usage.get("count", 0))
    except (ValueError, TypeError):
        count = 0
    if count >= MONTHLY_FREE_CAP:
        logger.warning(f"Gemini grounding monthly free cap ({MONTHLY_FREE_CAP}) reached.")
        return False
    usage["count"] = count + 1
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(usage, indent=2), encoding="utf-8")
    return True


def ask_grounded(question: str, model: str | None = None) -> dict[str, Any]:
    """Ask a single grounded research question. Returns {"text": str, "citations": [...]}.

    Uses GEMINI_GROUNDING_MODEL env var (defaults to 'gemini-3.5-flash-lite') if model is not provided.
    """
    if not _check_and_increment_quota():
        return {"text": "", "citations": []}
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set - skipping grounded search.")
        return {"text": "", "citations": []}
    target_model = model or os.getenv("GEMINI_GROUNDING_MODEL", DEFAULT_GROUNDING_MODEL)
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        interaction = client.interactions.create(
            model=target_model,
            input=question,
            tools=[{"type": "google_search"}],
        )
        text, citations = "", []
        seen_urls = set()
        for step in getattr(interaction, "steps", []) or []:
            if getattr(step, "type", None) == "model_output":
                for block in getattr(step, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        text += getattr(block, "text", "") or ""
                        for ann in getattr(block, "annotations", []) or []:
                            ann_type = getattr(ann, "type", None)
                            ann_url = getattr(ann, "url", "") or ""
                            ann_title = getattr(ann, "title", "") or ann_url
                            if ann_type == "url_citation" and not (
                                is_junk_url(ann_url) or is_junk_url(ann_title)
                            ):
                                if ann_url not in seen_urls:
                                    seen_urls.add(ann_url)
                                    citations.append({"title": ann_title, "url": ann_url})
        return {"text": text, "citations": citations}
    except Exception as e:
        logger.warning(f"Gemini grounded search failed for '{question}': {e}")
        return {"text": "", "citations": []}


def format_grounded_block(ticker: str, question: str, model: str | None = None) -> str:
    result = ask_grounded(question, model=model)
    res_text = str(result.get("text", ""))
    if not res_text:
        return f"--- GOOGLE-GROUNDED RESEARCH ({ticker}) ---\n(unavailable)\n"
    lines = [f"--- GOOGLE-GROUNDED RESEARCH ({ticker}) ---", res_text, ""]
    citations = result.get("citations", [])
    if isinstance(citations, list) and citations:
        lines.append("Sources:")
        for c in citations:
            if isinstance(c, dict):
                lines.append(f"  - {c.get('title')}: {c.get('url')}")
    return "\n".join(lines) + "\n"
