from datetime import datetime, timezone

import pytest

from app.tennis_abstract import (
    discover_fragment,
    parse_recent_fragment,
)


def test_discover_fragment_uses_current_url_and_checks_identity():
    page = b'''<html><head><title>Tennis Abstract: Jannik Sinner Match Results, Splits, and Analysis</title>
    <script src="https://www.tennisabstract.com/jsfrags/JannikSinner.js"></script></head></html>'''
    result = discover_fragment(page, "https://www.tennisabstract.com/cgi-bin/player.cgi?p=JannikSinner", "Sinner Jannik")
    assert result["source_player_id"] == "JannikSinner"
    assert result["fragment_url"].endswith("/jsfrags/JannikSinner.js")
    with pytest.raises(ValueError, match="identity_mismatch"):
        discover_fragment(page, "https://www.tennisabstract.com/cgi-bin/player.cgi?p=JannikSinner", "Novak Djokovic")


def test_discover_fragment_distinguishes_missing_from_ambiguous_capability():
    profile_url = "https://www.tennisabstract.com/cgi-bin/player.cgi?p=JannikSinner"
    no_fragment = b'''<html><head><title>Tennis Abstract: Jannik Sinner Match Results, Splits, and Analysis</title></head></html>'''
    with pytest.raises(ValueError, match="fragment_missing"):
        discover_fragment(no_fragment, profile_url, "Jannik Sinner")

    ambiguous = b'''<html><head><title>Tennis Abstract: Jannik Sinner Match Results, Splits, and Analysis</title>
    <script src="/jsfrags/JannikSinner.js"></script><script src="/jsfrags/JannikSinnerRecent.js"></script>
    </head></html>'''
    with pytest.raises(ValueError, match="fragment_ambiguous"):
        discover_fragment(ambiguous, profile_url, "Jannik Sinner")


def test_recent_fragment_preserves_partiality_and_raw_percentage_semantics():
    fragment = b'''<table id="recent-results"><tr><th>Date</th></tr>
    <tr><td>20-Oct-2026</td><td><a href="/cgi-bin/tourney.cgi?t=foo">Paris</a></td><td>Hard</td><td>F</td>
    <td>1</td><td>2</td><td><b>Sinner</b> d. <a href="/cgi-bin/player.cgi?p=NovakDjokovic">Novak Djokovic</a> [SRB]</td>
    <td>6-4 7-6(5)</td><td>1.2</td><td>10.0%</td><td>2.0%</td><td>65.0%</td><td>80.0%</td><td>55.0%</td><td>2/3</td><td>1:40</td></tr>
    <tr><td>19-Oct-2026</td><td>Paris</td><td>Hard</td><td>R16</td><td>1</td><td>3</td>
    <td><b>Alexander Zverev</b> d. <a href="/cgi-bin/player.cgi?p=JannikSinner">Sinner</a> [ITA]</td>
    <td>6-4 6-3</td><td>1.0</td><td></td><td></td><td>60.0%</td><td>70.0%</td><td>50.0%</td><td>1/2</td><td>1:20</td></tr></table>'''
    parsed = parse_recent_fragment(
        fragment, source_player_id="JannikSinner", expected_name="Jannik Sinner",
        fetched_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
    )
    assert len(parsed["matches"]) == 2
    assert [row["player_won"] for row in parsed["matches"]] == [True, False]
    assert [row["opponent_name"] for row in parsed["matches"]] == ["Novak Djokovic", "Alexander Zverev"]
    assert parsed["matches"][0]["stats"]["ace_pct"] == "10.0%"
    assert parsed["matches"][1]["stats"].get("ace_pct") is None
    assert parsed["matches"][0]["stats_semantics"].endswith("denominators_not_provided")
    assert parsed["history_scope"] == "recent_fragment_not_full_career"
    assert parsed["complete"] is False
    assert parsed["serve_statistics_usable"] is False


def test_recent_fragment_rejects_target_ambiguity_and_non_match_rows():
    fragment = b'''<table id="recent-results"><tr><th>Date</th></tr>
    <tr><td>20-Oct-2026</td><td>Paris</td><td>Hard</td><td>F</td><td>1</td><td>2</td>
    <td><b>Someone Else</b> d. <a href="/cgi-bin/player.cgi?p=OtherPlayer">Other Player</a></td>
    <td>6-4 6-3</td><td>1.2</td><td>10%</td><td>2%</td><td>65%</td><td>80%</td><td>55%</td><td>2/3</td><td>1:40</td></tr></table>'''
    parsed = parse_recent_fragment(fragment, source_player_id="JannikSinner", expected_name="Jannik Sinner")
    assert parsed["matches"] == []
    assert parsed["rows_rejected"] == 1
