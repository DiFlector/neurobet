"""Prediction scrapers registry."""

from .betzona import BetzonaScraper
from .stavka_tv import StavkaTvScraper
from .vseprosport import VseprosportScraper
from .nb_bet import NbBetScraper
from .kushvsporte import KushVsporteScraper
from .legalbet import LegalbetScraper
from .livesport import LivesportScraper
from .overbetting import OverbettingScraper
from .metaratings import MetaratingsScraper

ALL_SCRAPERS = [
    BetzonaScraper,
    StavkaTvScraper,
    VseprosportScraper,
    NbBetScraper,
    KushVsporteScraper,
    LegalbetScraper,
    LivesportScraper,
    OverbettingScraper,
    MetaratingsScraper,
]

__all__ = [
    "BetzonaScraper",
    "StavkaTvScraper",
    "VseprosportScraper",
    "NbBetScraper",
    "KushVsporteScraper",
    "LegalbetScraper",
    "LivesportScraper",
    "OverbettingScraper",
    "MetaratingsScraper",
    "ALL_SCRAPERS",
]
