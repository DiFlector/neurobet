"""Unit test suite for external prediction scrapers, normalization, matching, and 24h cleanup."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid
import pytest

from contracts import ExternalPredictionDTO, PredictionConsensusSummary
try:
    from app.predictions.base import (
        clean_player_name,
        normalize_tennis_player,
        parse_russian_datetime,
        standardize_market_outcome,
    )
    from app.predictions.scrapers.betzona import BetzonaScraper
    from app.predictions.scrapers.stavka_tv import StavkaTvScraper
    from app.predictions.scrapers.vseprosport import VseprosportScraper
    from app.predictions.scrapers.nb_bet import NbBetScraper
    from app.predictions.scrapers.kushvsporte import KushVsporteScraper
    from app.predictions.scrapers.legalbet import LegalbetScraper
    from app.predictions.scrapers.livesport import LivesportScraper
    from app.predictions.scrapers.overbetting import OverbettingScraper
    from app.predictions.scrapers.metaratings import MetaratingsScraper
    from app.predictions.manager import PredictionManager
except ImportError:
    from services.research.app.predictions.base import (
        clean_player_name,
        normalize_tennis_player,
        parse_russian_datetime,
        standardize_market_outcome,
    )
    from services.research.app.predictions.scrapers.betzona import BetzonaScraper
    from services.research.app.predictions.scrapers.stavka_tv import StavkaTvScraper
    from services.research.app.predictions.scrapers.vseprosport import VseprosportScraper
    from services.research.app.predictions.scrapers.nb_bet import NbBetScraper
    from services.research.app.predictions.scrapers.kushvsporte import KushVsporteScraper
    from services.research.app.predictions.scrapers.legalbet import LegalbetScraper
    from services.research.app.predictions.scrapers.livesport import LivesportScraper
    from services.research.app.predictions.scrapers.overbetting import OverbettingScraper
    from services.research.app.predictions.scrapers.metaratings import MetaratingsScraper
    from services.research.app.predictions.manager import PredictionManager


def test_clean_player_name():
    assert clean_player_name("Медведев Д. [1]") == "Медведев Д."
    assert clean_player_name("Джокович Н. (WC)") == "Джокович Н."
    assert clean_player_name("  - Алькарас К.  ") == "Алькарас К."


def test_normalize_tennis_player():
    assert normalize_tennis_player("медведев д") == "Даниил Медведев"
    assert normalize_tennis_player("Джокович Н.") == "Новак Джокович"
    assert normalize_tennis_player("Арнальди М.") == "Маттео Арнальди" or "Арнальди" in normalize_tennis_player("Арнальди М.")


def test_standardize_market_outcome():
    m, o = standardize_market_outcome("Победа 1 с учетом овертайма", "Медведев", "Джокович")
    assert m == "match_winner" and o == "p1"

    m, o = standardize_market_outcome("П2 за коэффициент 2.10", "Медведев", "Джокович")
    assert m == "match_winner" and o == "p2"

    m, o = standardize_market_outcome("Фора 1 (-3.5) по геймам", "Медведев", "Джокович")
    assert m == "handicap_games" and o == "f1_minus"

    m, o = standardize_market_outcome("Фора 2 (+4.5)", "Медведев", "Джокович")
    assert m == "handicap_games" and o == "f2_plus"

    m, o = standardize_market_outcome("Тотал больше (21.5)", "Медведев", "Джокович")
    assert m == "total_games" and o == "tb"


def test_parse_russian_datetime():
    ref_time = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
    
    dt_today = parse_russian_datetime("сегодня 17:30", now=ref_time)
    assert dt_today.day == 19 and dt_today.month == 10 and dt_today.hour == 17 and dt_today.minute == 30

    dt_tomorrow = parse_russian_datetime("завтра 11:00", now=ref_time)
    assert dt_tomorrow.day == 20 and dt_tomorrow.month == 10 and dt_tomorrow.hour == 11

    dt_exact = parse_russian_datetime("25 октября 14:15", now=ref_time)
    assert dt_exact.day == 25 and dt_exact.month == 10 and dt_exact.hour == 14 and dt_exact.minute == 15


def test_betzona_scraper_mock():
    mock_html = """
    <html>
        <a href="/m-arnaldi-l-muzetti-prognoz-1792285165.html">
            М.Арнальди - Л.Музетти
            19 октября 17:30 - Теннис. ATP 250
            1.68
        </a>
    </html>
    """
    scraper = BetzonaScraper()
    with patch.object(scraper, "fetch_url", return_value=mock_html):
        preds = scraper.scrape()
        assert len(preds) == 1
        p = preds[0]
        assert p.source_code == "betzona"
        assert "Арнальди" in p.raw_participant_a
        assert "Музетти" in p.raw_participant_b
        assert p.recommended_odds == 1.68
        assert p.estimated_start_at.month == 10 and p.estimated_start_at.day == 19


def test_stavka_tv_scraper_mock():
    mock_html = """
    <html>
        <a href="/matches/tennis/19-10-2026-golubic-viktorija-hontama-mai#tips-6">
            Голубич - Хонтама
        </a>
    </html>
    """
    scraper = StavkaTvScraper()
    with patch.object(scraper, "fetch_url", return_value=mock_html):
        preds = scraper.scrape()
        assert len(preds) == 1
        p = preds[0]
        assert p.source_code == "stavka_tv"
        assert p.estimated_start_at.day == 19 and p.estimated_start_at.month == 10
        assert p.author_name == "Stavka.tv Community"


def test_vseprosport_scraper_mock():
    mock_html = """
    <html>
        <a href="/news/2026/10/18/majja-hvalinska-sonaj-kartal-prognoz-kf-1-47-i-stavki-na-match-uhan-18-oktyabrya-2026-goda-ot-vps">
            Майя Хвалинска — Сонай Картал: прогноз
        </a>
    </html>
    """
    scraper = VseprosportScraper()
    with patch.object(scraper, "fetch_url", return_value=mock_html):
        preds = scraper.scrape()
        assert len(preds) == 1
        p = preds[0]
        assert p.source_code == "vseprosport"
        assert p.recommended_odds == 1.47
        assert p.estimated_start_at.day == 18 and p.estimated_start_at.month == 10


def test_nb_bet_scraper_mock():
    mock_html = """
    <html>
        <a href="https://nb-bet.com/tennis/events/758597-thiago-seyboth-wild-ugo-delen-prognoz-na-match">
            ATP Челленджер. Santa Cruz 2, Боливия Seyboth Wild T. Дельен У. сегодня 04:30
        </a>
    </html>
    """
    scraper = NbBetScraper()
    with patch.object(scraper, "fetch_url", return_value=mock_html):
        preds = scraper.scrape()
        assert len(preds) == 1
        p = preds[0]
        assert p.source_code == "nb_bet"
        assert "Челленджер" in p.tournament_name


def test_consensus_calculation_mock():
    """Verify consensus aggregator produces correct shares and majority pick."""
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = None
    mock_preds = [
        MagicMock(predicted_outcome="p1", recommended_odds=1.70, source_code="betzona", reasoning_snippet="Arg 1"),
        MagicMock(predicted_outcome="p1", recommended_odds=1.75, source_code="stavka_tv", reasoning_snippet="Arg 2"),
        MagicMock(predicted_outcome="p1", recommended_odds=1.72, source_code="vseprosport", reasoning_snippet="Arg 3"),
        MagicMock(predicted_outcome="p2", recommended_odds=2.20, source_code="legalbet", reasoning_snippet="Arg 4"),
    ]
    mock_session.execute.return_value.scalars.return_value.all.return_value = mock_preds

    manager = PredictionManager(session_factory=lambda: mock_session)
    ev_id = uuid.uuid4()
    summary = manager.get_consensus_for_event(ev_id)

    assert summary.total_predictions == 4
    assert summary.p1_count == 3
    assert summary.p2_count == 1
    assert summary.p1_share == 0.75
    assert summary.p2_share == 0.25
    assert summary.consensus_pick == "p1"
    assert len(summary.sources) == 4
    assert round(summary.avg_p1_odds, 2) == 1.72


def test_cleanup_expired_unmatched_query():
    """Verify 24h cleanup executes deletion for expired unmatched rows."""
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = None
    mock_session.execute.return_value.rowcount = 3
    manager = PredictionManager(session_factory=lambda: mock_session)

    deleted = manager.cleanup_expired_unmatched_predictions()
    assert deleted == 3
    assert mock_session.commit.called


if __name__ == "__main__":
    test_clean_player_name()
    test_normalize_tennis_player()
    test_standardize_market_outcome()
    test_parse_russian_datetime()
    test_betzona_scraper_mock()
    test_stavka_tv_scraper_mock()
    test_vseprosport_scraper_mock()
    test_nb_bet_scraper_mock()
    test_consensus_calculation_mock()
    test_cleanup_expired_unmatched_query()
    print("ALL PREDICTION TESTS PASSED SUCCESSFULLY! (10/10)")
