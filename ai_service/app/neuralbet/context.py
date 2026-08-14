"""
Static categorical context the model conditions on: which sport, and which family of
outcome (win/draw/win2, double chance, total, or "other") it's looking at. Without
this the GRU/LightGBM only ever see a bare odds/score curve — a score_diff of 3 means
"almost certainly over" in football and "meaningless noise" in basketball, and the
model has no way to tell those apart. See model.py's OddsTrajectoryGRU for how these
indices get embedded and concatenated onto the sequence encoder's output.

Both vocabularies are fixed and hardcoded (not learned/discovered at runtime): index
assignment has to stay stable across process restarts for a saved embedding table to
mean anything on reload, so this can't be an auto-growing dict.
"""
import hashlib
from typing import Optional

# ---------------------------------------------------------------------------
# Sport vocabulary — matched against the top-level segment of events.sport_path
# (a " / "-joined breadcrumb like "Футбол / Англия / Премьер-лига", built in
# backend/parser_service.py's get_sport_path()). Index 0 is reserved for
# "unknown/other sport" so anything not in this list still gets a valid embedding
# instead of crashing or aliasing onto a real sport.
# ---------------------------------------------------------------------------
SPORT_NAMES = [
    "другое",       # 0 — fallback bucket, keep first
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


def sport_index(sport_path: Optional[str]) -> int:
    if not sport_path:
        return 0
    top = str(sport_path).split("/")[0].strip().lower()
    return SPORT_VOCAB.get(top, 0)


# ---------------------------------------------------------------------------
# Market-family vocabulary — "what is this bet actually on," independent of sport.
# factor_id sets mirror CORE_FACTOR_MAP / FACTORS_* in backend/database.py (kept as a
# small duplicated constant here rather than importing across the service boundary —
# ai_service and backend are separate deployables with no shared package). Anything
# with a factor_id outside these known main-match outcomes (handicaps, part-of-match
# markets, sport-specific props, ...) falls into OTHER — still a real, distinct
# category the model can learn "this is some other/exotic market" from.
# ---------------------------------------------------------------------------
MARKET_FAMILIES = ["other", "w1", "draw", "w2", "double_chance_1x", "double_chance_12", "double_chance_x2", "total_over", "total_under"]
MARKET_FAMILY_VOCAB = {name: i for i, name in enumerate(MARKET_FAMILIES)}
NUM_MARKET_FAMILIES = len(MARKET_FAMILIES)

_FACTOR_TO_FAMILY = {
    921: "w1", 922: "draw", 923: "w2",
    # 925/1571 were swapped for a long time — Fonbet's own catalog (factorsCatalog/
    # sportBasicFactors) maps factor 925 to "X2" and 1571 to "12", confirmed against
    # live data (see backend/parser_service.py's CORE_FACTOR_MAP comment).
    924: "double_chance_1x", 1571: "double_chance_12", 925: "double_chance_x2", 926: "double_chance_x2",
    930: "total_over", 931: "total_under",
}


def market_family_index(factor_id: Optional[int]) -> int:
    if factor_id is None:
        return 0
    family = _FACTOR_TO_FAMILY.get(int(factor_id), "other")
    return MARKET_FAMILY_VOCAB[family]


# ---------------------------------------------------------------------------
# Team/player identity — unlike sport or market family, this isn't a small closed
# vocabulary: thousands of distinct teams and players appear across every league Fonbet
# covers, and new ones show up constantly (promotions, new tournaments, individual
# athletes in 1-on-1 sports like tennis/table tennis, where team_1/team_2 already hold
# the player's own name — same column, no separate handling needed). A fixed lookup
# table like SPORT_VOCAB would either need constant maintenance or silently alias every
# unseen name onto "unknown". Feature hashing sidesteps that: any name maps into a fixed
# embedding table via a stable hash, so the model can still learn "this specific team
# tends to X" for any name it's seen enough times, without ever needing to know the full
# vocabulary in advance. Collisions exist (two different names landing in the same
# bucket) but are rare enough at this table size to not matter in practice, and get
# rarer as this scales, unlike a fixed vocab which gets *worse* as more teams appear.
# hashlib (not Python's built-in hash()) because str hashing is randomized per-process
# by default — a saved embedding table would mean something different after every
# restart otherwise.
# ---------------------------------------------------------------------------
TEAM_HASH_BUCKETS = 3000


def team_index(name: Optional[str]) -> int:
    """Index 0 is reserved for "no/unknown name" (empty team_2 on an outright-style
    entry, or genuinely missing data) — real names hash into [1, TEAM_HASH_BUCKETS)."""
    name = (name or "").strip().lower()
    if not name:
        return 0
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % (TEAM_HASH_BUCKETS - 1)) + 1
