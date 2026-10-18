"""Base prediction scraper interface, normalization helpers, and outcome mappings."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import logging
import re
import ssl
import urllib.request
from typing import List, Optional, Tuple

from contracts import ExternalPredictionDTO

logger = logging.getLogger("research.predictions")

# Month names mapping in Russian
RU_MONTHS = {
    "января": 1, "янв": 1,
    "февраля": 2, "фев": 2,
    "марта": 3, "мар": 3,
    "апреля": 4, "апр": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июн": 6,
    "июля": 7, "июл": 7,
    "августа": 8, "авг": 8,
    "сентября": 9, "сен": 9,
    "октября": 10, "окт": 10,
    "ноября": 11, "ноя": 11,
    "декабря": 12, "дек": 12,
}


def clean_player_name(raw_name: str) -> str:
    """Removes tournament brackets, seeds, ranking prefixes, and punctuation."""
    if not raw_name:
        return ""
    name = str(raw_name).strip()
    # Remove qualifications/seeds like (1), [WC], (Q), [LL]
    name = re.sub(r"[\(\[\{].*?[\)\]\}]", "", name)
    name = name.strip(" ,-–—")
    name = re.sub(r"\s+", " ", name)
    return name.strip()


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


def normalize_tennis_player(raw_name: str) -> str:
    """Normalizes Russian or Latin player names to canonical title case."""
    cleaned = clean_player_name(raw_name)
    if not cleaned:
        return "Unknown Player"

    lookup_key = cleaned.lower().replace("ё", "е").replace(".", "").strip()
    lookup_key = re.sub(r"\s+", " ", lookup_key)

    if lookup_key in CANONICAL_PLAYERS:
        return CANONICAL_PLAYERS[lookup_key]

    parts = lookup_key.split()
    if len(parts) == 2 and len(parts[0]) == 1:
        rev_key = f"{parts[1]} {parts[0]}"
        if rev_key in CANONICAL_PLAYERS:
            return CANONICAL_PLAYERS[rev_key]

    return " ".join(part.capitalize() for part in cleaned.split())


def standardize_market_outcome(raw_text: str, player_a: str, player_b: str) -> Tuple[str, str]:
    """
    Classifies raw prediction string into (market_type, predicted_outcome).
    Returns tuple of (market_type, predicted_outcome).
    """
    text = (raw_text or "").strip().lower()
    
    # 1. Match Winner (P1 / P2)
    if re.search(r'\b(п1|победа\s*(1|перв)|w1|победа\s+к1)\b', text):
        return "match_winner", "p1"
    if re.search(r'\b(п2|победа\s*(2|втор)|w2|победа\s+к2)\b', text):
        return "match_winner", "p2"
    
    # Check if player name is explicitly in selection text
    pa_surname = player_a.split()[-1].lower() if player_a else ""
    pb_surname = player_b.split()[-1].lower() if player_b else ""
    if pa_surname and pa_surname in text and pb_surname not in text:
        return "match_winner", "p1"
    if pb_surname and pb_surname in text and pa_surname not in text:
        return "match_winner", "p2"

    # 2. Handicaps
    if "ф1" in text or "фора 1" in text or "фора1" in text:
        return "handicap_games", "f1_plus" if "+" in text else "f1_minus"
    if "ф2" in text or "фора 2" in text or "фора2" in text:
        return "handicap_games", "f2_plus" if "+" in text else "f2_minus"

    # 3. Totals
    if "тб" in text or "тотал больше" in text or "больше" in text:
        return "total_games", "tb"
    if "тм" in text or "тотал меньше" in text or "меньше" in text:
        return "total_games", "tm"

    # Default fallback
    return "other", "unknown"


def parse_russian_datetime(date_str: str, now: Optional[datetime] = None) -> datetime:
    """Parses date/time strings like '19 октября 17:30', 'сегодня 04:30', 'завтра 12:00'."""
    now = now or datetime.now(timezone.utc)
    s = date_str.lower().strip()

    # Time extraction: HH:MM
    time_match = re.search(r'(\d{1,2})[:\.](\d{2})', s)
    hour = int(time_match.group(1)) if time_match else 12
    minute = int(time_match.group(2)) if time_match else 0

    if "сегодня" in s:
        target_date = now.date()
    elif "завтра" in s:
        target_date = (now + timedelta(days=1)).date()
    elif "вчера" in s:
        target_date = (now - timedelta(days=1)).date()
    else:
        # Check day + month: e.g. "19 октября" or "19.10"
        dm_match = re.search(r'(\d{1,2})\s+([а-я]+)', s)
        if dm_match and dm_match.group(2) in RU_MONTHS:
            day = int(dm_match.group(1))
            month = RU_MONTHS[dm_match.group(2)]
            year = now.year
            # if month < now.month - 6, maybe next year
            try:
                target_date = datetime(year, month, day).date()
            except ValueError:
                target_date = now.date()
        else:
            num_match = re.search(r'(\d{1,2})[\./-](\d{1,2})(?:[\./-](\d{2,4}))?', s)
            if num_match:
                day = int(num_match.group(1))
                month = int(num_match.group(2))
                year = int(num_match.group(3)) if num_match.group(3) else now.year
                if year < 100:
                    year += 2000
                try:
                    target_date = datetime(year, month, day).date()
                except ValueError:
                    target_date = now.date()
            else:
                target_date = now.date()

    dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=timezone.utc)
    return dt


class BasePredictionScraper(ABC):
    """Abstract base class for portal-specific prediction scrapers."""

    source_code: str = "base"
    base_url: str = ""
    default_timeout: int = 8

    def __init__(self):
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    def fetch_url(self, url: str) -> Optional[str]:
        """Safely fetches HTML content from url."""
        try:
            req = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=self.default_timeout, context=self._ctx) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="ignore")
                logger.warning(f"[{self.source_code}] HTTP status {resp.status} for {url}")
                return None
        except Exception as e:
            logger.warning(f"[{self.source_code}] Fetch failed for {url}: {e}")
            return None

    @abstractmethod
    def scrape(self) -> List[ExternalPredictionDTO]:
        """Scrapes predictions from the target portal."""
        pass
