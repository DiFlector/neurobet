"""Tennis playing surface classification from Russian tournament names and metadata."""

import re
from typing import Any, Dict, Optional


SURFACE_KEYWORDS = {
    "clay": [
        "грунт",
        "глина",
        "ролан гаррос",
        "мадрид",
        "рим",
        "монте-карло",
        "барселона",
        "буэнос-айрес",
        "рио-де-жанейро",
        "гштаад",
        "умaг",
        "кицбюэль",
        "clay",
        "red clay",
    ],
    "grass": [
        "трава",
        "травяной",
        "уимблдон",
        "халле",
        "лондон",
        "куинс",
        "штутгарт",
        "ньюпорт",
        "майорка",
        "хертогенбос",
        "истборн",
        "grass",
    ],
    "carpet": [
        "ковер",
        "ковёр",
        "carpet",
        "синтетика",
    ],
    "hard": [
        "хард",
        "hard",
        "открытый хард",
        "закрытый хард",
        "outdoor hard",
        "indoor hard",
        "австралиан опен",
        "us open",
        "индиан-уэллс",
        "майами",
        "цинциннати",
        "шанхай",
        "пекин",
        "токио",
        "париж",
        "доха",
        "дубай",
    ],
}


def detect_tennis_surface(tournament_name: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Detects tennis court surface ('hard', 'clay', 'grass', 'carpet').
    Defaults to 'hard' if unspecified or undetectable.
    """
    if metadata and metadata.get("surface"):
        surf = str(metadata["surface"]).lower().strip()
        for canonical, keywords in SURFACE_KEYWORDS.items():
            if surf in keywords or any(k in surf for k in keywords):
                return canonical

    name_lower = (tournament_name or "").lower().strip()

    # Match against known keywords
    for canonical in ("clay", "grass", "carpet", "hard"):
        for kw in SURFACE_KEYWORDS[canonical]:
            pattern = r"(?:\b|_)" + re.escape(kw) + r"(?:\b|_)"
            if re.search(pattern, name_lower) or kw in name_lower:
                return canonical

    return "hard"
