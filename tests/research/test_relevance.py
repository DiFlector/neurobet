"""Unit tests for research relevance / noise filtering."""

from app.relevance import (
    MIN_RELEVANCE,
    score_relevance,
    should_keep,
    signal_about_participants,
)


def test_both_participants_high_relevance():
    score, meta = score_relevance(
        title="Medvedev vs Djokovic preview",
        snippet="H2H stats ahead of the China Open semi-final.",
        url="https://www.tennis.com/news/medvedev-djokovic",
        participant_a="Daniil Medvedev",
        participant_b="Novak Djokovic",
    )
    assert meta["hit_a"] and meta["hit_b"]
    assert score >= 0.7
    assert should_keep(score, meta)


def test_noise_stream_page_dropped():
    score, meta = score_relevance(
        title="Джокович — Медведев: онлайн-трансляция",
        snippet="Смотреть онлайн трансляцию матча. Где смотреть.",
        url="https://www.sport-express.ru/tennis/atp/online/foo",
        participant_a="Даниил Медведев",
        participant_b="Новак Джокович",
    )
    assert meta["noise"] is True
    assert should_keep(score, meta) is False


def test_unrelated_player_article_low():
    score, meta = score_relevance(
        title="Djokovic knocks out Bu",
        snippet="Novak Djokovic beat Yunchaokete Bu in Beijing.",
        url="https://www.tennismajors.com/atp/djokovic-bu",
        participant_a="Daniil Medvedev",
        participant_b="Novak Djokovic",
    )
    assert meta["hit_b"] is True
    assert meta["hit_a"] is False
    assert score < 0.7


def test_injury_only_near_participant():
    # Травма de Minaur — не сигнал для пары Medvedev/Djokovic
    assert (
        signal_about_participants(
            "Djokovic wins as injured de Minaur retires",
            "Alex de Minaur retired injured.",
            ["Daniil Medvedev", "Novak Djokovic"],
            ["injury", "retire", "injured"],
        )
        is False
    )
    # Травма Медведева — сигнал
    assert (
        signal_about_participants(
            "Medvedev injury concern before Djokovic clash",
            "Daniil Medvedev dealt with a shoulder injury in practice.",
            ["Daniil Medvedev", "Novak Djokovic"],
            ["injury", "травм"],
        )
        is True
    )


def test_min_relevance_constant():
    assert 0.0 < MIN_RELEVANCE < 0.5


if __name__ == "__main__":
    test_both_participants_high_relevance()
    test_noise_stream_page_dropped()
    test_unrelated_player_article_low()
    test_injury_only_near_participant()
    test_min_relevance_constant()
    print("ALL RELEVANCE TESTS OK")
