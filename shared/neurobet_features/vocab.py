"""Fixed sport / market / team vocabularies for embeddings and LightGBM.

Index assignment has to stay stable across restarts for a saved embedding
table to mean anything, so this is hardcoded — not discovered at runtime.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from neurobet_filters import TOTAL_OVER_IDS, TOTAL_UNDER_IDS, sport_top

SPORT_NAMES = [
    "другое",
    "футбол",
    "хоккей",
    "баскетбол",
    "теннис",
    "волейбол",
    "настольный теннис",
    "киберспорт",
    "гандбол",
    "бейсбол",
    "американский футбол",
    "регби",
    "снукер",
    "дартс",
    "бадминтон",
    "крикет",
    "футзал",
    "водное поло",
    "пляжный футбол",
    "пляжный волейбол",
]
SPORT_VOCAB = {name: i for i, name in enumerate(SPORT_NAMES)}
NUM_SPORTS = len(SPORT_NAMES)

MARKET_FAMILIES = [
    "other", "w1", "draw", "w2",
    "double_chance_1x", "double_chance_12", "double_chance_x2",
    "total_over", "total_under",
]
MARKET_FAMILY_VOCAB = {name: i for i, name in enumerate(MARKET_FAMILIES)}
NUM_MARKET_FAMILIES = len(MARKET_FAMILIES)

_FACTOR_TO_FAMILY = {
    921: "w1", 922: "draw", 923: "w2",
    924: "double_chance_1x", 1571: "double_chance_12",
    925: "double_chance_x2", 926: "double_chance_x2",
    **{fid: "total_over" for fid in TOTAL_OVER_IDS},
    **{fid: "total_under" for fid in TOTAL_UNDER_IDS},
}

TEAM_HASH_BUCKETS = 3000


def sport_index(sport_path: Optional[str]) -> int:
    if not sport_path:
        return 0
    return SPORT_VOCAB.get(sport_top(sport_path), 0)


def market_family_index(factor_id: Optional[int]) -> int:
    if factor_id is None:
        return 0
    family = _FACTOR_TO_FAMILY.get(int(factor_id), "other")
    return MARKET_FAMILY_VOCAB[family]


def team_index(name: Optional[str]) -> int:
    """Index 0 = missing name; real names hash into [1, TEAM_HASH_BUCKETS)."""
    name = (name or "").strip().lower()
    if not name:
        return 0
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % (TEAM_HASH_BUCKETS - 1)) + 1
