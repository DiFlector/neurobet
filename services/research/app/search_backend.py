"""Search query formulation and search backend integrations."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import config
from .domain_filter import DomainFilter

logger = logging.getLogger("research.search")

# Простая транслитерация кириллицы → латиница для англоязычных источников.
_CYR_MAP = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


def _transliterate(text: str) -> str:
    lowered = text.lower().translate(_CYR_MAP)
    return re.sub(r"\s+", " ", lowered).strip()


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[\u0400-\u04FF]", text))


class SearchBackend:
    """Manages sports search query generation and URL discovery."""

    # Приоритет доменов: чаще отдают нормальные превью/новости.
    PRIORITY_DOMAINS = (
        "sports.ru",
        "championat.com",
        "sport-express.ru",
        "tennis.com",
        "atptour.com",
        "wtatennis.com",
        "tennismajors.com",
        "espn.com",
        "bbc.com",
        "theguardian.com",
        "reuters.com",
        "flashscore.com",
    )

    def __init__(self, backend: Optional[str] = None):
        self.backend = (backend or config.search_backend or "ddg").strip().lower()
        self._mock_search_results: Dict[str, List[str]] = {}

    def register_mock_query(self, query: str, urls: List[str]) -> None:
        """Registers deterministic search results for a query (tests)."""
        self._mock_search_results[query] = urls

    @staticmethod
    def generate_tennis_queries(
        player_a: str, player_b: str, tournament: str = None
    ) -> List[str]:
        """Backward-compatible tennis query helper."""
        return SearchBackend.generate_queries("tennis", player_a, player_b, tournament)

    @staticmethod
    def generate_queries(
        sport_code: str,
        participant_a: str,
        participant_b: str,
        tournament: str = None,
    ) -> List[str]:
        """Focused search queries for match context (injuries, form, preview)."""
        sport = (sport_code or "tennis").strip().lower() or "tennis"
        a = (participant_a or "").strip()
        b = (participant_b or "").strip()
        # Сначала карточки матча sports.ru — H2H/форма/факты.
        queries: List[str] = [
            f"site:sports.ru/{sport}/match {a} {b}",
            f"site:sports.ru {a} {b} матч",
            f"{a} vs {b} {sport}",
            f"{a} {b} {sport} injury OR withdrawal OR preview",
        ]
        if tournament:
            queries.append(f"{a} {tournament} {sport} form")
        # Латинский вариант для англоязычных сайтов allowlist.
        if _has_cyrillic(a) or _has_cyrillic(b):
            la, lb = _transliterate(a), _transliterate(b)
            if la and lb:
                queries.insert(1, f"site:sports.ru/{sport}/match {la} {lb}")
                queries.append(f"{la} vs {lb} {sport}")
                queries.append(f"{la} {sport} injury")
        # Уникальные, без пустых
        seen = set()
        out: List[str] = []
        for q in queries:
            key = " ".join(q.lower().split())
            if key and key not in seen:
                seen.add(key)
                out.append(q)
        return out

    def search(self, query: str, max_results: int = 5) -> List[str]:
        """
        Executes search and returns candidate URLs filtered through domain allowlist.
        Never invents fake article URLs.

        Backend modes:
          - searxng / ddg — один движок
          - hybrid / both / all — SearXNG + DuckDuckGo параллельно, merge+dedupe
        """
        if query in self._mock_search_results:
            results = self._mock_search_results[query]
            return [u for u in results if DomainFilter.is_allowed(u)[0]][:max_results]

        if self.backend in ("mock", "none", "off"):
            logger.info("Search backend=%s — returning no URLs for '%s'", self.backend, query)
            return []

        engines = self._resolve_engines()
        per_engine = max(max_results * 2, 6)
        merged: List[str] = []
        seen: set[str] = set()

        hybrid = len(engines) > 1

        def _run(name: str) -> Tuple[str, List[str]]:
            try:
                if name == "searxng":
                    return name, self._searxng_search(query, max_results=per_engine)
                # В hybrid — короткий DDG (1–2 попытки), иначе полный обход доменов.
                return name, self._ddg_search(
                    query, max_results=per_engine, fast=hybrid
                )
            except Exception as exc:
                logger.warning("Search engine %s failed for '%s': %s", name, query[:60], exc)
                return name, []

        # Параллельный прогон — быстрее полный пакет для LLM-вердикта.
        with ThreadPoolExecutor(max_workers=max(1, len(engines))) as pool:
            futures = [pool.submit(_run, name) for name in engines]
            for fut in as_completed(futures):
                name, urls = fut.result()
                added = 0
                for url in urls:
                    if not url or url in seen:
                        continue
                    allowed, _reason = DomainFilter.is_allowed(url)
                    if not allowed:
                        continue
                    seen.add(url)
                    merged.append(url)
                    added += 1
                logger.info(
                    "Search engine=%s query='%s' → %d raw, +%d unique",
                    name,
                    query[:60],
                    len(urls),
                    added,
                )

        # Лёгкая пересортировка: приоритетные домены раньше.
        rank = {d: i for i, d in enumerate(self.PRIORITY_DOMAINS)}

        def _rank(u: str) -> int:
            return rank.get(DomainFilter.extract_domain(u), 100)

        merged.sort(key=_rank)
        return merged[: max(max_results * 2, max_results)]

    def _resolve_engines(self) -> List[str]:
        mode = self.backend
        if mode in ("hybrid", "both", "all"):
            engines = ["searxng", "ddg"]
        elif mode == "searxng":
            engines = ["searxng"]
        else:
            engines = ["ddg"]
        # Без URL SearXNG — не дергаем пустой движок.
        if "searxng" in engines and not (config.searxng_url or "").strip():
            engines = [e for e in engines if e != "searxng"]
            if not engines:
                engines = ["ddg"]
        return engines

    def _priority_domains(self) -> List[str]:
        allow = config.allowlist
        ordered = [d for d in self.PRIORITY_DOMAINS if d in allow]
        rest = sorted(d for d in allow if d not in ordered)
        return ordered + rest

    def _ddg_search(
        self, query: str, max_results: int = 10, fast: bool = False
    ) -> List[str]:
        """DuckDuckGo text search restricted to allowlisted domains."""
        ddgs_cls = self._load_ddgs()
        if ddgs_cls is None:
            logger.warning("ddgs/duckduckgo-search not installed; no search results")
            return []

        domains = self._priority_domains()[:8]
        urls: List[str] = []
        seen = set()

        # Сначала объединённый site:-запрос; в полном режиме — ещё точечные домены.
        site_clause = " OR ".join(f"site:{d}" for d in domains)
        attempts = [f"{query} ({site_clause})"] if domains else [query]
        if not fast:
            for domain in domains[:4]:
                attempts.append(f"{query} site:{domain}")
        else:
            # Один быстрый добор по русским СМИ (обычно без anti-bot).
            for domain in ("sports.ru", "sport-express.ru"):
                if domain in domains:
                    attempts.append(f"{query} site:{domain}")
                    break

        ddgs = ddgs_cls()
        try:
            for attempt in attempts:
                if len(urls) >= max_results:
                    break
                try:
                    rows = list(
                        ddgs.text(
                            attempt,
                            max_results=min(8, max_results),
                            region="wt-wt",
                            safesearch="moderate",
                        )
                    )
                except TypeError:
                    # Older ddgs API without region/safesearch kwargs.
                    try:
                        rows = list(ddgs.text(attempt, max_results=min(8, max_results)))
                    except Exception as exc:
                        logger.info("DDG attempt failed (%s): %s", attempt[:60], exc)
                        continue
                except Exception as exc:
                    logger.info("DDG attempt failed (%s): %s", attempt[:60], exc)
                    continue
                for row in rows or []:
                    href = (row.get("href") or row.get("link") or "").strip()
                    if not href or href in seen:
                        continue
                    seen.add(href)
                    urls.append(href)
                    if len(urls) >= max_results:
                        break
        finally:
            closer = getattr(ddgs, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        logger.info("DDG search for '%s' → %d url(s)", query[:80], len(urls))
        return urls

    def _searxng_search(self, query: str, max_results: int = 10) -> List[str]:
        """SearXNG JSON search (RESEARCH_SEARXNG_URL)."""
        base = (config.searxng_url or "").rstrip("/")
        if not base:
            logger.warning("RESEARCH_SEARXNG_URL empty; cannot use searxng backend")
            return []

        domains = self._priority_domains()[:8]
        site_clause = " OR ".join(f"site:{d}" for d in domains)
        full_query = f"{query} ({site_clause})" if domains else query
        params = {
            "q": full_query,
            "format": "json",
            "language": "auto",
            "safesearch": 1,
            "pageno": 1,
            "categories": "general,news",
        }
        url = f"{base}/search"
        # Чуть длиннее timeout: SearXNG агрегирует несколько движков.
        timeout = max(float(config.timeout_seconds), 15.0)
        headers = {
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "Accept": "application/json",
        }
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 403:
                logger.warning(
                    "SearXNG JSON forbidden (403). Check search.formats includes json in settings.yml"
                )
            resp.raise_for_status()
            payload: Dict[str, Any] = resp.json()
        results = payload.get("results") or []
        urls = []
        for item in results:
            href = (item.get("url") or "").strip()
            if href:
                urls.append(href)
            if len(urls) >= max_results:
                break
        # Fallback без site: — иногда движки плохо едят длинный OR.
        if not urls and domains:
            params["q"] = query
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
            for item in payload.get("results") or []:
                href = (item.get("url") or "").strip()
                if href:
                    urls.append(href)
                if len(urls) >= max_results:
                    break
        logger.info("SearXNG search for '%s' → %d url(s)", query[:80], len(urls))
        return urls

    @staticmethod
    def _load_ddgs():
        try:
            from ddgs import DDGS  # type: ignore

            return DDGS
        except ImportError:
            pass
        try:
            from duckduckgo_search import DDGS  # type: ignore

            return DDGS
        except ImportError:
            return None
