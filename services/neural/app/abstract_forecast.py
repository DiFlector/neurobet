"""Match-win probability from a Tennis Abstract tournament forecast.

The number is read from the current-round table. It is not inferred from
injury keywords.
"""

from __future__ import annotations

import gzip
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.request import Request, urlopen

logger = logging.getLogger("neural.abstract")

_HOME = "https://www.tennisabstract.com/"
_CACHE_SECONDS = 1800
_cache: Dict[str, Tuple[float, str]] = {}
_result_cache: Dict[Tuple[str, str, str], Tuple[float, Optional[float]]] = {}

_PAGE_RE = re.compile(
    r"<b>([^<]+)</b>\s*<br\s*/?>\s*<a href=\"(https://www\.tennisabstract\.com/current/\d{4}[^\"]+\.html)\"",
    re.IGNORECASE,
)

# Fonbet city names that do not transliterate into the English page title.
_CITY = {
    "ухань": "wuhan",
    "шанхай": "shanghai",
    "цзинань": "jinan",
    "майя": "maia",
    "ольбия": "olbia",
    "роан": "roanne",
    "лиссабон": "lisbon",
    "роверето": "rovereto",
    "мальорка": "mallorca",
    "палермо": "palermo",
    "брага": "braga",
    "антофагаста": "antofagasta",
    "вильена": "villena",
    "катания": "catania",
    "сантакрус": "santacruz",
    "унин": "wuning",
    "пекин": "beijing",
    "индианвеллс": "indianwells",
    "майами": "miami",
    "рим": "rome",
    "мадрид": "madrid",
    "париж": "paris",
    "уимблдон": "wimbledon",
}

_NOISE = {
    "wta", "atp", "itf", "world", "tennis", "теннис", "челленджер", "challenger",
    "женщины", "женщин", "мужчины", "мужчин", "пары", "хард", "грунт", "трава",
    "hard", "clay", "grass", "live", "k",
}


def list_current_pages(home_html: str) -> List[Tuple[str, str]]:
    """(label, url) for forecast pages linked from the Tennis Abstract home page."""
    found: List[Tuple[str, str]] = []
    seen = set()
    for label, url in _PAGE_RE.findall(home_html or ""):
        clean = " ".join(label.split())
        if url in seen or not clean:
            continue
        seen.add(url)
        found.append((clean, url))
    return found


def choose_page(tournament: str, pages: Sequence[Tuple[str, str]]) -> Optional[str]:
    """Pick the current forecast page for a Fonbet tournament title."""
    text = (tournament or "").lower().replace("ё", "е")
    if not text or "пар" in text:
        return None
    keys, digits = _place_keys(text)
    if not keys:
        return None
    best_url = None
    best_score = 0
    for label, url in pages:
        if not _tour_ok(text, label):
            continue
        blob = f"{label} {url}".lower()
        if digits and not all(digit in blob for digit in digits):
            continue
        score = sum(len(key) for key in keys if key in blob)
        if score > best_score:
            best_score = score
            best_url = url
    return best_url if best_score else None


def parse_match_probability(html: str, name_a: str, name_b: str) -> Optional[float]:
    """Probability that name_a wins the current match, from var projCurrent."""
    match = re.search(r"var\s+projCurrent\s*=\s*'(.*?)';", html or "", re.S)
    if not match:
        return None
    rows = []
    for row in re.findall(r"<tr>(.*?)</tr>", match.group(1), re.S):
        name = re.search(r">([^<]+)</a>", row)
        percents = re.findall(r"(\d+\.\d)\s*%", row)
        if name and percents:
            rows.append((name.group(1).strip(), float(percents[0]) / 100.0))
    index = 0
    while index < len(rows) - 1:
        left_name, left_p = rows[index]
        right_name, right_p = rows[index + 1]
        if abs(left_p + right_p - 1.0) > 0.02:
            index += 1
            continue
        side_a_left = _name_score(name_a, left_name)
        side_a_right = _name_score(name_a, right_name)
        side_b_left = _name_score(name_b, left_name)
        side_b_right = _name_score(name_b, right_name)
        if side_a_left >= 0.70 and side_b_right >= 0.70 and side_a_left > side_a_right and side_b_right >= side_b_left:
            return round(left_p, 4)
        if side_a_right >= 0.70 and side_b_left >= 0.70 and side_a_right > side_a_left and side_b_left >= side_b_right:
            return round(right_p, 4)
        index += 2
    return None


def match_win_probability(tournament: str, name_a: str, name_b: str) -> Optional[float]:
    """Live fetch. None when the pair is not on a current forecast page."""
    if not (name_a or "").strip() or not (name_b or "").strip():
        return None
    if "пар" in (tournament or "").lower():
        return None
    key = (
        " ".join((tournament or "").lower().split()),
        " ".join(name_a.lower().split()),
        " ".join(name_b.lower().split()),
    )
    cached = _result_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    try:
        value = _lookup_probability(tournament or "", name_a, name_b)
    except Exception as exc:
        logger.info("Tennis Abstract forecast unavailable: %s", exc)
        value = None
    _result_cache[key] = (now, value)
    if value is not None:
        logger.info("Tennis Abstract P(%s)=%.3f", name_a, value)
    return value


def _lookup_probability(tournament: str, name_a: str, name_b: str) -> Optional[float]:
    pages = list_current_pages(_fetch(_HOME))
    chosen = choose_page(tournament, pages)
    if chosen:
        probability = parse_match_probability(_fetch(chosen), name_a, name_b)
        if probability is not None or tournament.strip():
            return probability
    for _label, url in pages:
        if url == chosen:
            continue
        try:
            html = _fetch(url)
        except Exception as exc:
            logger.info("Tennis Abstract page skipped %s: %s", url, exc)
            continue
        probability = parse_match_probability(html, name_a, name_b)
        if probability is not None:
            return probability
    return None


def _place_keys(text: str) -> Tuple[List[str], List[str]]:
    compact = text.replace("-", "").replace("ё", "е")
    words = re.findall(r"[a-zа-я0-9]+", compact)
    keys: List[str] = []
    digits: List[str] = []
    for word in words:
        if word.isdigit():
            digits.append(word)
            continue
        if word in _NOISE or len(word) < 4:
            continue
        if word.isdigit() is False and word.endswith("k") and word[:-1].isdigit():
            continue
        english = _CITY.get(word) or _transliterate(word)
        if len(english) >= 4:
            keys.append(english)
    return keys, digits


def _tour_ok(tournament: str, label: str) -> bool:
    lab = label.lower()
    challenger = "челлендж" in tournament or "challenger" in tournament
    wta = "wta" in tournament or "женщин" in tournament
    atp = "atp" in tournament or "мужчин" in tournament
    if challenger:
        return "challenger" in lab
    if wta and not atp:
        return "wta" in lab and "challenger" not in lab
    if atp and not wta:
        return "atp" in lab and "wta" not in lab
    return "challenger" not in lab


def _name_score(left: str, right: str) -> float:
    a = _surname(left)
    b = _surname(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    if len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5]:
        return 1.0
    folded_a = _fold_surname(a)
    folded_b = _fold_surname(b)
    return max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, folded_a, folded_b).ratio(),
    )


def _fold_surname(text: str) -> str:
    folded = text.replace("shch", "s").replace("sh", "s").replace("ch", "h")
    return folded.replace("w", "v").replace("o", "a").replace("y", "i").replace("j", "i")


def _surname(name: str) -> str:
    try:
        from tennis_adapter.external_names import surname_and_initial

        surname, _initial = surname_and_initial(name)
        if surname:
            return surname
    except ImportError:
        pass
    parts = [part for part in _transliterate(name).split() if len(part) > 1]
    if not parts:
        return ""
    if len(parts) >= 2 and len(parts[-1]) == 1:
        return parts[0]
    return parts[-1] if len(parts[-1]) > 1 else parts[0]


def _transliterate(text: str) -> str:
    try:
        from tennis_adapter.external_names import transliterate

        return transliterate(text)
    except ImportError:
        return (text or "").lower()


def _fetch(url: str) -> str:
    now = time.monotonic()
    cached = _cache.get(url)
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"},
    )
    with urlopen(req, timeout=12) as resp:
        raw = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    body = raw.decode("utf-8", "replace")
    _cache[url] = (now, body)
    return body
