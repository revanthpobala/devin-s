"""WATCH-candidate ordering hook.

Historically this ranked WATCH candidates competing for paid research slots with a
LightGBM booster (``RANK_MODEL_ENABLED``). The learned path was removed: it never
affected PASS/WATCH/CUT, required an out-of-band LightGBM artifact + ``full_v2``
training corpus, and the dependency is not installed. Callers still import
``score_data_window`` for the ``rank_model_score`` field, so this now returns None
and they fall back to the deterministic ``deep_research_sort_key`` / tiebreak order.
"""

from typing import Dict, Optional


def score_data_window(f: Dict[str, Optional[float]]) -> Optional[float]:
    """Return None; callers fall back to deterministic ordering."""
    return None
