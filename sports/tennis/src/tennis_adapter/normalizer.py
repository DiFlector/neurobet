"""Russian tennis player name normalizer and alias resolver."""

import re


# Canonical dictionary for common ATP & WTA players in Russian
CANONICAL_PLAYERS = {
    # ATP Stars
    "джокович н": "Новак Джокович",
    "новак джокович": "Новак Джокович",
    "алькарас к": "Карлос Алькарас",
    "алькарас гарфия к": "Карлос Алькарас",
    "карлос алькарас": "Карлос Алькарас",
    "синнер я": "Янник Синнер",
    "янник синнер": "Янник Синнер",
    "медведев д": "Даниил Медведев",
    "даниил медведев": "Даниил Медведев",
    "рублев а": "Андрей Рублев",
    "рублёв а": "Андрей Рублев",
    "андрей рублев": "Андрей Рублев",
    "хачанов к": "Карен Хачанов",
    "карен хачанов": "Карен Хачанов",
    "зверев а": "Александр Зверев",
    "александр зверев": "Александр Зверев",
    "циципас с": "Стефанос Циципас",
    "стефанос циципас": "Стефанос Циципас",
    "фриц т": "Тейлор Фриц",
    "тейлор фриц": "Тейлор Фриц",
    "рууд к": "Каспер Рууд",
    "каспер рууд": "Каспер Рууд",
    "димитров г": "Григор Димитров",
    "григор димитров": "Григор Димитров",
    "де минор а": "Алекс де Минор",
    "алекс де минор": "Алекс де Минор",
    "тиафо ф": "Фрэнсис Тиафо",
    "фрэнсис тиафо": "Фрэнсис Тиафо",
    "попырин а": "Алексей Попырин",
    "алексей попырин": "Алексей Попырин",
    "сафиуллин р": "Роман Сафиуллин",
    "роман сафиуллин": "Роман Сафиуллин",
    "фис а": "Артур Фис",
    "артур фис": "Артур Фис",
    "мунaр х": "Хауме Мунар",
    "мунар х": "Хауме Мунар",

    # WTA Stars
    "соболенко а": "Арина Соболенко",
    "арина соболенко": "Арина Соболенко",
    "швентек и": "Ига Швентек",
    "свёнтек и": "Ига Швентек",
    "ига швентек": "Ига Швентек",
    "рыбакина е": "Елена Рыбакина",
    "елена рыбакина": "Елена Рыбакина",
    "гауфф к": "Кори Гауфф",
    "кори гауфф": "Кори Гауфф",
    "пегула д": "Джессика Пегула",
    "джессика пегула": "Джессика Пегула",
    "касаткина д": "Дарья Касаткина",
    "дарья касаткина": "Дарья Касаткина",
    "андреева м": "Мирра Андреева",
    "мирра андреева": "Мирра Андреева",
    "самсонова л": "Людмила Самсонова",
    "людмила самсонова": "Людмила Самсонова",
}


def clean_player_name(raw_name: str) -> str:
    """Removes tournament brackets, seeds, ranking prefixes, and trailing punctuation."""
    if not raw_name:
        return ""

    name = str(raw_name).strip()
    # Remove qualifications/seeds like (1), [WC], (Q), [LL], etc.
    name = re.sub(r"[\(\[\{].*?[\)\]\}]", "", name)
    # Remove trailing/leading punctuation
    name = name.strip(" .,-")
    # Replace multiple spaces with single space
    name = re.sub(r"\s+", " ", name)
    return name


def normalize_russian_player_name(raw_name: str) -> str:
    """
    Normalizes a Russian tennis player name to canonical form.
    E.g. 'Медведев Д.' -> 'Даниил Медведев', 'Джокович Н.' -> 'Новак Джокович'.
    """
    cleaned = clean_player_name(raw_name)
    if not cleaned:
        return "Неизвестный игрок"

    # Normalize letter 'ё' to 'е' and lowercase for dictionary lookup
    lookup_key = cleaned.lower().replace("ё", "е").replace(".", "").strip()
    lookup_key = re.sub(r"\s+", " ", lookup_key)

    if lookup_key in CANONICAL_PLAYERS:
        return CANONICAL_PLAYERS[lookup_key]

    # Handle 'И. Фамилия' -> 'Фамилия И.' reversal for lookup
    parts = lookup_key.split()
    if len(parts) == 2 and len(parts[0]) == 1:
        rev_key = f"{parts[1]} {parts[0]}"
        if rev_key in CANONICAL_PLAYERS:
            return CANONICAL_PLAYERS[rev_key]

    # Fallback: return cleaned name in proper Title Case
    return " ".join(part.capitalize() for part in cleaned.split())
