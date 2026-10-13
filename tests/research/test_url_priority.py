"""Tests for match-hub URL priority (sports.ru/tennis/match/…)."""

from app.url_priority import is_match_hub, prioritize_urls, split_match_hubs
from app.relevance import is_noise, score_relevance, should_keep


def test_detect_sports_ru_match_hub():
    assert is_match_hub("https://www.sports.ru/tennis/match/1992883/") is True
    assert is_match_hub("https://www.sports.ru/tennis/player/hurkacz/") is False
    assert is_match_hub("https://www.tennismajors.com/atp/news-foo") is False


def test_match_hubs_sorted_first():
    urls = [
        "https://www.tennismajors.com/atp/hurkacz-djokovic",
        "https://www.sports.ru/tennis/match/1992883/",
        "https://www.espn.com/tennis/story/x",
    ]
    ordered = prioritize_urls(urls, domain_order=("sports.ru", "tennismajors.com", "espn.com"))
    assert ordered[0].endswith("/1992883/")
    hubs, rest = split_match_hubs(urls)
    assert len(hubs) == 1 and len(rest) == 2


def test_match_hub_not_noise_despite_broadcast_title():
    url = "https://www.sports.ru/tennis/match/1992883/"
    assert is_noise("Хуркач – Давидович-Фокина: онлайн-трансляция", "H2H факты", url) is False
    score, meta = score_relevance(
        title="Хуркач – Давидович-Фокина: онлайн-трансляция",
        snippet="Алехандро Давидович-Фокина побеждает в 3 последних очных. Фокина травмировался.",
        url=url,
        participant_a="Хуркач Х",
        participant_b="Давидович Фокина А",
    )
    assert meta["match_hub"] is True
    assert should_keep(score, meta) is True
    assert score >= 0.5


if __name__ == "__main__":
    test_detect_sports_ru_match_hub()
    test_match_hubs_sorted_first()
    test_match_hub_not_noise_despite_broadcast_title()
    print("ALL URL PRIORITY TESTS OK")
