"""Re-exports the shared vocab / overround / universe helpers.

The source of truth is `neurobet_features` (and `neurobet_filters` for the
universe). This module stays import-compatible for model.py / pipeline.py.
"""
import sys
from pathlib import Path

_here = Path(__file__).resolve()
for _parent in _here.parents:
    _shared = _parent / "shared"
    if (_shared / "neurobet_filters").is_dir():
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        break

from neurobet_filters import (  # noqa: E402
    in_universe as in_train_universe,
    universe_sql,
    universe_sql_params,
    universe_line_sql,
    ALLOWED_SPORTS,
    ALLOWED_FACTOR_IDS,
    DRAW_FACTOR_ID,
    TOTAL_LINE_RANGES,
    sport_top,
    parse_total_line,
)
from neurobet_features import (  # noqa: E402
    NUM_SPORTS,
    NUM_MARKET_FAMILIES,
    SPORT_NAMES,
    MARKET_FAMILIES,
    TEAM_HASH_BUCKETS,
    sport_index,
    market_family_index,
    team_index,
    OVERROUND_EXPECTED_SIZE,
    overround_group_key,
)
