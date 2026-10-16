"""Relevance / noise scoring for research evidence against match participants."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Set, Tuple

# Транслит для сопоставления «Медведев» ↔ medvedev в англ. источниках.
_CYR_MAP = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)

_NOISE_PATTERNS = (
    r"онлайн[- ]?трансляц",
    r"смотреть\s+онлайн",
    r"live\s*score",
    r"point by point",
    r"расписание.{0,20}сегодня",
    r"теннис\s+сегодня",
    r"где\s+смотреть",
    r"матч[- ]центр",
    r"/online/",
    r"/live/",
)

_SIGNAL_PATTERNS = (
    r"травм",
    r"injur",  # injury / injured
    r"withdraw",
    r"retir",  # retired / retires
    r"дисквал",
    r"disqualif",
    r"preview",
    r"превью",
    r"h2h",
    r"личн\w+\s+встреч",
    r"\bform\b",
    r"усталост",
    r"fatigue",
)

# Минимальный relevance, чтобы попасть в пакет для LLM.
MIN_RELEVANCE = 0.35


def _transliterate(text: str) -> str:
    return text.lower().translate(_CYR_MAP)


def participant_tokens(name: str) -> Set[str]:
    """Surname-ish tokens (>=3 chars) + latin transliteration variants."""
    raw = (name or "").strip()
    if not raw:
        return set()
    # Убираем слэши парного разряда → отдельные фамилии.
    parts = re.split(r"[/|,]+", raw)
    tokens: Set[str] = set()
    for part in parts:
        words = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", part)
        for w in words:
            low = w.lower()
            tokens.add(low)
            lat = _transliterate(low)
            if len(lat) >= 3:
                tokens.add(lat)
            # Укороченный корень без окончаний (медведев → медведе)
            if len(low) >= 5:
                tokens.add(low[:-1])
                if len(lat) >= 5:
                    tokens.add(lat[:-1])
    # Отбрасываем слишком общие токены
    stop = {"the", "and", "vs", "атп", "wta", "atp", "теннис", "tennis"}
    return {t for t in tokens if t not in stop and len(t) >= 3}


def _contains_token(text: str, tokens: Set[str]) -> bool:
    if not tokens:
        return False
    blob = text.lower()
    blob_lat = _transliterate(blob)
    for tok in tokens:
        if tok in blob or tok in blob_lat:
            return True
    return False


_VERSUS_SPLIT = re.compile(
    r"\s+(?:vs\.?|v\.?|против)\s+|[–—/]|(?<!\w)\s-\s",
    re.IGNORECASE,
)
_PERSON_URL = re.compile(r"/person/([^/?#]+)", re.IGNORECASE)


def _fold_name(token: str) -> str:
    """Латиница и испанское -ez/-es, чтобы «Мартинес» совпал с Martinez."""
    folded = _transliterate((token or "").lower())
    if folded.endswith("ez"):
        folded = folded[:-2] + "es"
    return folded


def _name_words(text: str) -> List[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁё]+", text or "")


def _parse_participant(name: str) -> Tuple[Set[str], Set[str]]:
    """Фамильные токены и допустимые первые буквы имени.

    «Мартинес Т» — фамилия и инициал. «Daniil Medvedev» — фамилия последняя,
    буква имени берётся из первого слова.
    """
    words = _name_words(name)
    initials = [w for w in words if len(w) == 1]
    longs = [w for w in words if len(w) >= 3]
    if initials or len(longs) < 2:
        surname_words = longs
        given_words = []
    else:
        given_words = [longs[0]]
        surname_words = longs[1:]
    surnames = {_fold_name(w) for w in surname_words if len(_fold_name(w)) >= 4}
    letters: Set[str] = set()
    for w in initials + given_words:
        low = w.lower()
        letters.add(low[0])
        lat = _transliterate(low)
        if lat:
            letters.add(lat[0])
    return surnames, letters


def _is_surname_word(word: str, surnames: Set[str]) -> bool:
    folded = _fold_name(word)
    return len(folded) >= 4 and folded in surnames


def _given_matches(word: str, letters: Set[str]) -> bool:
    if not letters:
        return True
    low = word.lower()
    if low[0] in letters:
        return True
    lat = _transliterate(low)
    return bool(lat) and lat[0] in letters


def _subject_text(title: str, url: str) -> str:
    """Имя, о котором страница: slug /person/ или заголовок до двоеточия."""
    match = _PERSON_URL.search(url or "")
    if match:
        return match.group(1).replace("-", " ").replace("_", " ")
    return re.split(r"[:|]", title or "", maxsplit=1)[0]


def identity_conflict(participant: str, title: str, url: str = "") -> bool:
    """Страница о другом человеке с той же фамилией.

    «Мартинес Т» не совпадает с «Педро Мартинес» и «Кончита Мартинес».
    Фамилия без чужого имени рядом конфликтом не считается.
    """
    surnames, letters = _parse_participant(participant)
    if not surnames or not letters:
        return False
    sides = [part for part in _VERSUS_SPLIT.split(_subject_text(title, url)) if part.strip()]
    saw_conflict = False
    saw_compatible = False
    for side in sides:
        words = _name_words(side)
        for index, word in enumerate(words):
            if not _is_surname_word(word, surnames):
                continue
            if index == 0 or _is_surname_word(words[index - 1], surnames):
                saw_compatible = True
                continue
            if _given_matches(words[index - 1], letters):
                saw_compatible = True
            else:
                saw_conflict = True
    return saw_conflict and not saw_compatible


def is_noise(title: str, snippet: str, url: str = "") -> bool:
    # Карточка матча sports.ru часто называется «онлайн-трансляция», но внутри
    # H2H/форма/статистика — это не шум, а приоритетный источник.
    try:
        from .url_priority import is_match_hub
    except ImportError:
        is_match_hub = lambda _u: False  # noqa: E731
    if is_match_hub(url):
        return False
    blob = f"{title} {snippet} {url}".lower()
    return any(re.search(p, blob, flags=re.IGNORECASE) for p in _NOISE_PATTERNS)


def has_quality_signal(title: str, snippet: str) -> bool:
    blob = f"{title} {snippet}".lower()
    return any(re.search(p, blob, flags=re.IGNORECASE) for p in _SIGNAL_PATTERNS)


def score_relevance(
    *,
    title: str,
    snippet: str,
    url: str,
    participant_a: str,
    participant_b: str,
) -> Tuple[float, dict]:
    """
    Честный relevance [0..1] относительно участников матча.
    Возвращает (score, debug_meta).
    """
    tokens_a = participant_tokens(participant_a)
    tokens_b = participant_tokens(participant_b)
    text = f"{title} {snippet}"

    conflict_a = identity_conflict(participant_a, title, url)
    conflict_b = identity_conflict(participant_b, title, url)
    hit_a = _contains_token(text, tokens_a) and not conflict_a
    hit_b = _contains_token(text, tokens_b) and not conflict_b
    noise = is_noise(title, snippet, url)
    signal = has_quality_signal(title, snippet)

    score = 0.0
    if hit_a:
        score += 0.38
    if hit_b:
        score += 0.38
    if hit_a and hit_b:
        score += 0.14
    if signal:
        score += 0.12
    try:
        from .url_priority import is_match_hub
    except ImportError:
        is_match_hub = lambda _u: False  # noqa: E731
    match_hub = is_match_hub(url)
    if noise:
        score -= 0.45
    # URL path с /online/ /live/ — штраф (не для match hub)
    url_l = (url or "").lower()
    if not match_hub and ("/online/" in url_l or "/live/" in url_l or "translyac" in url_l):
        score -= 0.15

    about_match = signal_about_participants(
        title,
        snippet,
        [participant_a, participant_b],
        ["травм", "injur", "retir", "withdraw", "дисквал", "disqualif", "усталост", "fatigue"],
    )
    score = max(0.0, min(1.0, score))
    meta = {
        "hit_a": hit_a,
        "hit_b": hit_b,
        "noise": noise,
        "signal": signal,
        "signal_about_match": about_match,
        "match_hub": match_hub,
        "conflict_a": conflict_a,
        "conflict_b": conflict_b,
    }
    return score, meta


def should_keep(relevance: float, meta: dict) -> bool:
    """Отбрасываем шум и документы без привязки к участникам."""
    if relevance < MIN_RELEVANCE:
        return False
    hit_a, hit_b = bool(meta.get("hit_a")), bool(meta.get("hit_b"))
    if not hit_a and not hit_b:
        return False
    # Чистые «онлайн/трансляция» без качественного сигнала — вон.
    if meta.get("noise") and not meta.get("signal"):
        return False
    if meta.get("noise") and not (hit_a and hit_b):
        return False
    # Оба участника — ок.
    if hit_a and hit_b:
        return True
    # Карточка матча sports.ru с хотя бы одним участником — приоритетный keep.
    if meta.get("match_hub") and (hit_a or hit_b) and relevance >= MIN_RELEVANCE:
        return True
    # Один участник — только если сигнал про него (не травма третьего в статье).
    if meta.get("signal_about_match") and relevance >= 0.45:
        return True
    return False


def _subject_matches_tokens(subject: str, tokens: Set[str]) -> bool:
    sub = subject.lower().strip()
    sub_lat = _transliterate(sub)
    return any(tok in sub or tok in sub_lat for tok in tokens)


def signal_about_participants(
    title: str,
    snippet: str,
    participant_names: Sequence[str],
    keywords: Iterable[str],
    window_words: int = 4,
) -> bool:
    """
    True если сигнал (травма/усталость) относится к участнику матча.

    1) Явный субъект: «injured NAME», «NAME retired», «дисквалифицировали NAME»
    2) Иначе — keyword в ±N словах от токена участника, без чужого субъекта рядом.
    """
    text = f"{title} {snippet}"
    low = text.lower()
    tokens: Set[str] = set()
    for name in participant_names:
        tokens |= participant_tokens(name)
    if not tokens:
        return False

    # Явные субъекты действия/состояния
    subject_patterns = [
        r"injured\s+([a-zа-яё]+(?:[\s\-]+[a-zа-яё]+)?)",
        r"([a-zа-яё]+(?:[\s\-]+[a-zа-яё]+)?)\s+(?:retired|withdrawn|withdrew| scratched)",
        r"травм\w*\s+(?:у\s+)?([a-zа-яё]+)",
        r"([a-zа-яё]+)\s+травм",
        r"дисквалифицир\w*\s+([a-zа-яё]+)",
        r"([a-zа-яё]+)\s+дисквалифицир",
    ]
    subjects: List[str] = []
    for pat in subject_patterns:
        for m in re.finditer(pat, low, flags=re.IGNORECASE):
            subjects.append(m.group(1))
    if subjects:
        return any(_subject_matches_tokens(s, tokens) for s in subjects)

    # Fallback: узкая близость к токену участника
    words = re.findall(r"[a-zа-яё0-9]+", low)
    kw_set = {k.lower() for k in keywords}
    for i, w in enumerate(words):
        if not any(kw in w or w.startswith(kw) for kw in kw_set):
            continue
        lo, hi = max(0, i - window_words), min(len(words), i + window_words + 1)
        window = " ".join(words[lo:hi])
        window_lat = _transliterate(window)
        if any(tok in window or tok in window_lat for tok in tokens):
            return True
    return False


def rank_evidence(items: List[tuple]) -> List[tuple]:
    """Сортировка (relevance, freshness, item) по убыванию полезности."""
    return sorted(items, key=lambda row: (row[0], row[1]), reverse=True)
