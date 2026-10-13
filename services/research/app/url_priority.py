"""URL priority: match hub pages (sports.ru/tennis/match/…) first."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple
from urllib.parse import urlparse

# Карточки матча: H2H, форма, факты, статистика — выше обычных новостей.
_MATCH_HUB_PATTERNS = (
    re.compile(r"sports\.ru/(?:tennis|football|hockey|basketball|volleyball)/match/\d+", re.I),
    re.compile(r"sports\.ru/match/\d+", re.I),
    re.compile(r"championat\.com/.+/match(?:es)?/", re.I),
    re.compile(r"flashscore\.[a-z.]+/.+/match/", re.I),
    re.compile(r"sofascore\.com/.+/match/", re.I),
)


def is_match_hub(url: str) -> bool:
    """True for dedicated match pages (not generic news)."""
    if not url:
        return False
    u = url.strip()
    return any(p.search(u) for p in _MATCH_HUB_PATTERNS)


def split_match_hubs(urls: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Split into (match_hubs, other) preserving order, deduped."""
    hubs: List[str] = []
    other: List[str] = []
    seen = set()
    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        if is_match_hub(u):
            hubs.append(u)
        else:
            other.append(u)
    return hubs, other


def prioritize_urls(urls: Iterable[str], domain_order: Sequence[str] = ()) -> List[str]:
    """
    Sort: match hubs first, then by preferred domain order.
    «Если есть карточка матча — смотреть её, иначе идти дальше».
    """
    rank = {d: i for i, d in enumerate(domain_order)}

    def host(u: str) -> str:
        try:
            h = urlparse(u).netloc.lower()
        except Exception:
            return ""
        if h.startswith("www."):
            h = h[4:]
        return h

    def key(u: str) -> Tuple[int, int, str]:
        hub = 0 if is_match_hub(u) else 1
        dom = rank.get(host(u), 100)
        return (hub, dom, u)

    seen = set()
    unique: List[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique.append(u)
    return sorted(unique, key=key)
