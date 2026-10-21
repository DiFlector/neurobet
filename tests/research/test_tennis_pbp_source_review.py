from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "research"))
from tennis_pbp_source_review import _parsed_point_states_by_game


def test_source_parity_includes_ambiguous_extracted_score_without_confirming_it():
    events = [
        {"set": 3, "game_score": [4, 3], "point_score": ["30", "0"],
         "quality": "ambiguous", "confirmed": False, "tie_break": False},
        {"set": 3, "game_score": [4, 3], "point_score": ["30", "15"],
         "quality": "confirmed", "confirmed": True, "tie_break": False},
        {"set": 3, "game_score": [4, 3], "point_score": None,
         "quality": "terminal_from_game_result", "confirmed": True, "tie_break": False},
        {"set": 3, "game_score": [4, 3], "point_score": ["1", "0"],
         "quality": "confirmed", "confirmed": True, "tie_break": True},
    ]

    indexed = _parsed_point_states_by_game(events)

    assert list(indexed) == [(3, (4, 3))]
    assert [item["point_score"] for item in indexed[(3, (4, 3))]] == [["30", "0"], ["30", "15"]]
    assert indexed[(3, (4, 3))][0]["confirmed"] is False
