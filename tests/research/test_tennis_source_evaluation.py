"""Regressions for mistakes exposed by real alternative-source samples."""
import importlib.util
from pathlib import Path

script = Path(__file__).resolve().parents[2] / "scripts/research/evaluate_tennis_sources.py"
spec = importlib.util.spec_from_file_location("source_evaluation", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_futures_without_link_does_not_inherit_challenger_or_tiebreak_superscript():
    page = b'''<table class="result">
    <tr class="head flags"><td class="t-name"><a href="/rome/2026/atp-men/">Rome challenger</a></td></tr>
    <tr class="head flags"><td class="t-name">Futures 2026</td></tr>
    <tr><td class="t-name"><a href="/player/a/">Alpha A.</a></td><td class="result">2</td>
      <td class="score">7<sup>5</sup></td><td class="score">6</td><td><a href="/match-detail/?id=123">info</a></td></tr>
    <tr><td class="t-name"><a href="/player/b/">Beta B.</a></td><td class="result">0</td>
      <td class="score">6</td><td class="score">3</td></tr>
    <tr class="head flags"><td class="t-name"><a href="/rome/2026/atp-men/?type=double">Rome challenger</a></td></tr>
    <tr><td class="t-name"><a href="/player/a/">Alpha A.</a></td><td class="result">2</td><td><a href="/match-detail/?id=999">info</a></td></tr>
    <tr><td class="t-name"><a href="/player/b/">Beta B.</a></td><td class="result">0</td></tr></table>'''
    rows = module.tennisexplorer_results(page)
    assert len(rows) == 1
    assert rows[0]["tour"] == "itf"
    assert rows[0]["games"] == [[7, 6], [6, 3]]


def test_legacy_tennisabstract_http_200_can_still_be_years_old():
    result = module.ta_summary(b'var matchmx = [["20181119", "Andria CH"]];')
    assert result["latest_provider_date"] == "20181119"
    assert result["rows"] == 1


def test_api_game_final_scores_do_not_establish_explicit_point_completeness():
    event = {"scores": [{"score_set": "1", "score_first": "6", "score_second": "0"}],
             "pointbypoint": [{"set_number": "Set 1", "score": "6 - 0", "points": [{"score": "40 - 0"}]}]}
    result = module.api_point_shape(event, serve_points=2)
    assert result["game_end_scores_equal_final_sets"] is True
    assert result["raw_rows_equal_serve_points"] is False
    assert result["naive_rows_plus_one_per_source_entry_equal_serve_points"] is True
    assert result["terminal_points_are_not_explicitly_certified"] is True
