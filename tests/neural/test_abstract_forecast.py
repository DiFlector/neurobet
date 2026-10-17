"""Tennis Abstract current-round probability, without a network call."""

from app.abstract_forecast import choose_page, parse_match_probability

HOME = """
<b>WTA Wuhan</b><br/><a href="https://www.tennisabstract.com/current/2026WTAWuhan.html">Results</a>
<b>Jinan Challenger</b><br/><a href="https://www.tennisabstract.com/current/2026JinanChallenger.html">Results</a>
<b>WTA Lisbon 125</b><br/><a href="https://www.tennisabstract.com/current/2026WTALisbon125.html">Results</a>
<b>Santa Cruz de la Sierra 2 Challenger</b><br/>
<a href="https://www.tennisabstract.com/current/2026SantaCruzdelaSierra2Challenger.html">Results</a>
"""

PAGE = """
var projCurrent = '<table><tr><td>Player</td><td>F</td><td>W</td></tr>
<tr><td><a href="x">Iga Swiatek</a> (POL)</td><td>88.5%</td><td>73.3%</td></tr>
<tr><td><a href="x">Maja Chwalinska</a> (POL)</td><td>11.5%</td><td>4.5%</td></tr>
<tr><td><a href="x">Sonay Kartal</a> (GBR)</td><td>57.4%</td><td>13.8%</td></tr>
<tr><td><a href="x">Jelena Ostapenko</a> (LAT)</td><td>42.6%</td><td>8.4%</td></tr>
</table>';
"""


def test_choose_wuhan_and_skip_doubles():
    from app.abstract_forecast import list_current_pages

    pages = list_current_pages(HOME)
    assert choose_page("WTA. Ухань. Хард", pages).endswith("2026WTAWuhan.html")
    assert choose_page("ATP Челленджер. Цзинань", pages).endswith("2026JinanChallenger.html")
    assert choose_page("WTA 125K. Лиссабон", pages).endswith("2026WTALisbon125.html")
    assert "SantaCruz" in choose_page("ATP Челленджер. Санта-Крус 2", pages)
    assert choose_page("World Tennis. Женщины. Эдмонд", pages) is None
    assert choose_page("WTA. Ухань. Пары", pages) is None


def test_parse_pair_probability_for_side_a():
    assert parse_match_probability(PAGE, "Картал С", "Остапенко Е") == 0.574
    assert parse_match_probability(PAGE, "Остапенко Е", "Картал С") == 0.426
    assert parse_match_probability(PAGE, "Свёнтек И", "Хвалиньская М") == 0.885
    assert parse_match_probability(PAGE, "Ига Швентек", "Хвалинска М") == 0.885
    assert parse_match_probability(PAGE, "Неизвестный И", "Картал С") is None


def test_saved_side_is_the_match_feature():
    from types import SimpleNamespace

    from app.context_gate import abstract_side_from_decisions, context_side_from_event

    event = SimpleNamespace(metadata_json={"raw_tournament": "WTA. Ухань", "context_side_a": 0.574})
    assert context_side_from_event(event) == 0.574
    assert context_side_from_event(SimpleNamespace(metadata_json={})) is None
    rows = [
        SimpleNamespace(raw_json={"context_side_a": 0.5, "context_role": "pre_model"}),
        SimpleNamespace(
            raw_json={"context_side_a": 0.55, "context_side_source": "tennis_abstract", "context_role": "pre_model"}
        ),
    ]
    assert abstract_side_from_decisions(list(reversed(rows))) == 0.55


if __name__ == "__main__":
    test_choose_wuhan_and_skip_doubles()
    test_parse_pair_probability_for_side_a()
    print("ABSTRACT FORECAST TESTS OK")
