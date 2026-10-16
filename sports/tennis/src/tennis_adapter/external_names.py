"""Name keys shared by the external import and the Fonbet linker.

A Fonbet label looks like «Медведев Д». External files use «Daniil Medvedev»
or «Medvedev D.». Matching is on the surname and the initial.
"""

import re
import unicodedata
from typing import Optional, Tuple

_RU = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_INITIALS = {
    "а": "a", "б": "b", "в": "vw", "г": "g", "д": "d", "е": "ey", "ё": "e",
    "ж": "jg", "з": "z", "и": "i", "й": "yi", "к": "kc", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "hjk", "ц": "ct", "ч": "c", "ш": "s", "щ": "s",
    "э": "e", "ю": "yu", "я": "yji",
}
_PARTICLES = {"de", "del", "della", "di", "da", "van", "von", "der", "den", "la", "le", "st"}


def fold(text: str) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("ё", "е")
    raw = re.sub(r"[^a-zа-я0-9\s]", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def transliterate(text: str) -> str:
    out = []
    for ch in fold(text):
        out.append(_RU.get(ch, ch))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def surname_and_initial(name: str) -> Tuple[str, str]:
    """Latin surname and one-letter initial. Empty initial if the name has no initial."""
    latin = transliterate(name)
    parts = [part for part in latin.split() if part not in _PARTICLES]
    if not parts:
        return "", ""
    if len(parts) >= 2 and len(parts[-1]) == 1:
        surname_parts = [part for part in parts if len(part) > 1]
        initial = next(part for part in parts if len(part) == 1)
        if surname_parts:
            return " ".join(surname_parts), initial
    if len(parts) >= 2 and len(parts[0]) == 1:
        return " ".join(parts[1:]), parts[0]
    if len(parts) >= 2:
        return parts[-1], parts[0][:1]
    return parts[0], ""


def initial_matches(fonbet_initial: str, external_initial: str) -> bool:
    token = fold(fonbet_initial)[:1]
    other = fold(external_initial)[:1]
    if not token or not other:
        return False
    if token == other:
        return True
    allowed = _INITIALS.get(token, token)
    return other in allowed


def dedupe_token(name: str) -> str:
    surname, _initial = surname_and_initial(name)
    return surname or transliterate(name)


def choose_link(named: list, surname_only: list):
    """Return (identity, confidence) when exactly one player fits.

    `named` is the surname-and-initial search. `surname_only` is used only when
    that search is empty and the surname plus gender identify one player.
    Several candidates stay unlinked.
    """
    identities = {item[0] for item in named}
    if len(identities) == 1:
        return named[0][0], max(item[1] for item in named)
    if identities:
        return None
    only = {item[0] for item in surname_only}
    if len(only) == 1:
        return surname_only[0][0], 0.6
    return None
