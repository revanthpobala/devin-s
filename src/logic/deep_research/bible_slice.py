"""Static slim bible for the Pass 2 system prompt.

Sections that explain how the indicator is built (architecture, clustering math, opacity, formulas, verification log)
do not help read a chart or Data Window. Dropping them is a fixed cut, identical on every run, so llama.cpp's prefix
cache still hits. Per-ticker retrieval would change the prompt each run and throw that cache away.
"""

from __future__ import annotations

import os
import re

DROP_HEADINGS = (
    "Table of Contents",
    "1. System Architecture",
    "5. Clustering Algorithm",
    "7. Visual Opacity Logic",
    "15. Appendix: Formula Reference",
    "Verification Record",
)


def slim_bible(text: str) -> str:
    if os.getenv("BIBLE_FULL", "").strip() in ("1", "true", "yes"):
        return text
    parts = re.split(r"(?m)^(?=## )", text)
    keep = [p for p in parts if not any(p.startswith(f"## {h}") for h in DROP_HEADINGS)]
    return "".join(keep)
