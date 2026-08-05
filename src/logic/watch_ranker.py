"""Learned ordering for the WATCH candidates that compete for the paid research slots.

Ranking ONLY -- this score must never influence PASS/WATCH/CUT. Measured on the
full_v2 corpus, walk-forward OOS by year: +0.65pp 21d excess per pick over
deep_research_sort_key (SIG, and SIG in both 2016-2020 and 2021-2026 halves),
+0.56pp over random selection from the same WATCH set. See
data-windows/scripts/rank_watch_oos.py. Population caveat: measured on the whole
545-name S&P500 WATCH pool, not the ~15 mega-caps actually traded, so expect the
live gain to compress.

Returns None on any missing artifact, import failure or malformed input; callers
fall back to tiebreak_score.

Input MUST be the output of parse_data_window(), never a raw scraped dict. A raw dict
uses TradingView labels ("Buy Score") rather than field keys ("buy"), so every lookup
misses, and the booster still returns a plausible-looking float built from an all-NaN
row instead of failing loudly.
"""

import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Anchored to BASE_DIR, not cwd: the pipeline is launched from several entry points
# (main.py, run_local_research.py, scripts/) and a relative path silently resolves to
# a different place per launcher, which reads as "model missing" and falls back.
try:
    from src import config

    _DEFAULT_MODEL_DIR = str(config.BASE_DIR / "data" / "models")
except Exception:
    _DEFAULT_MODEL_DIR = os.path.join("data", "models")

MODEL_DIR = os.getenv("RANK_MODEL_DIR", _DEFAULT_MODEL_DIR)
ENABLED = os.getenv("RANK_MODEL_ENABLED", "1").lower() not in ("0", "false", "no")

_booster = None
_features: Optional[List[str]] = None
_load_failed = False


def _load():
    global _booster, _features, _load_failed
    if _booster is not None or _load_failed:
        return
    try:
        import lightgbm as lgb

        mp = os.path.join(MODEL_DIR, "watch_ranker.txt")
        fp = os.path.join(MODEL_DIR, "watch_ranker_features.json")
        with open(fp, "r", encoding="utf-8") as fh:
            _features = json.load(fh)["features"]
        _booster = lgb.Booster(model_file=mp)
        logger.info(f"watch_ranker loaded: {len(_features)} features from {mp}")
    except Exception as e:
        _load_failed = True
        logger.warning(f"watch_ranker unavailable, falling back to tiebreak_score: {e}")


def score_data_window(f: Dict[str, Optional[float]]) -> Optional[float]:
    """Model score for one parsed data window (output of parse_data_window)."""
    if not ENABLED:
        return None
    _load()
    if _booster is None or not _features:
        return None
    try:
        close = f.get("price")
        row = []
        for name in _features:
            if name.startswith("vs_"):
                lvl = f.get(name[3:])
                row.append((lvl / close - 1.0) * 100.0 if (lvl is not None and close) else float("nan"))
            else:
                v = f.get(name)
                row.append(float("nan") if v is None else float(v))
        import numpy as np

        return float(_booster.predict(np.array([row], dtype=np.float64))[0])
    except Exception as e:
        logger.debug(f"watch_ranker scoring failed: {e}")
        return None
