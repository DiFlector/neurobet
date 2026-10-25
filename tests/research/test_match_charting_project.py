import pytest

from app.match_charting import iter_point_documents, parse_match_metadata


def test_match_metadata_is_retrospective_without_inventing_result():
    body = (
        "match_id,Player 1,Player 2,Date,Tournament,Round,Surface,Best of\n"
        "20260521-M-RG-Q3-Player_A-Player_B,Player A,Player B,2026-05-21,Roland Garros,Q3,Clay,3\n"
    ).encode()
    [row] = parse_match_metadata(body, gender="m")
    assert row["source_match_id"] == "20260521-M-RG-Q3-Player_A-Player_B"
    assert row["payload"]["retrospective"] is True
    assert row["payload"]["charting_sample"] is True
    assert row["payload"]["winner"] is None
    assert row["payload"]["score"] is None


def test_charting_points_keep_confirmed_state_and_sampled_coverage():
    body = (
        "match_id,Pt,Set1,Set2,Gm1,Gm2,Pts,Gm#,TbSet,Svr,1st,2nd,Notes,PtWinner\n"
        "match-1,1,0,0,0,0,15-0,1,False,1,4f,,wide,1\n"
        "match-1,2,0,0,0,0,15-15,1,False,1,4b,,rally,2\n"
    ).encode()
    [document] = list(iter_point_documents(body))
    assert document["source_match_id"] == "match-1"
    assert len(document["events"]) == 2
    assert [event["winner"] for event in document["events"]] == [0, 1]
    assert all(event["confirmed"] for event in document["events"])
    assert document["events"][0]["server"] == 0
    assert document["events"][0]["shot_sequence"] == "4f"
    assert document["quality"]["complete"] is False
    assert document["quality"]["completeness"] == "selected_match_chart"


def test_regular_deuce_is_not_mislabeled_as_a_tiebreak():
    body = (
        "match_id,Pt,Set1,Set2,Gm1,Gm2,Pts,Gm#,TbSet,Svr,1st,2nd,Notes,PtWinner\n"
        "match-1,1,0,0,6,6,40-40,13,True,1,,,,1\n"
        "match-1,2,0,0,6,6,AD-40,13,True,1,,,,1\n"
    ).encode()
    [document] = list(iter_point_documents(body))
    assert not any(event["tie_break"] for event in document["events"])
    assert document["events"][1]["confirmed"] is True


def test_tiebreak_score_uses_numeric_transitions():
    body = (
        "match_id,Pt,Set1,Set2,Gm1,Gm2,Pts,Gm#,TbSet,Svr,1st,2nd,Notes,PtWinner\n"
        "match-1,1,0,0,6,6,1-0,13,True,1,,,,1\n"
        "match-1,2,0,0,6,6,1-1,13,True,2,,,,2\n"
    ).encode()
    [document] = list(iter_point_documents(body))
    assert [event["tie_break"] for event in document["events"]] == [True, True]
    assert all(event["confirmed"] for event in document["events"])


def test_charted_match_rows_must_be_contiguous_and_csv_contract_valid():
    body = (
        "match_id,Pt,Set1,Set2,Gm1,Gm2,Pts,Gm#,TbSet,Svr,1st,2nd,Notes,PtWinner\n"
        "match-1,1,0,0,0,0,15-0,1,False,1,,,,1\n"
        "match-2,1,0,0,0,0,15-0,1,False,1,,,,1\n"
        "match-1,2,0,0,0,0,15-15,1,False,1,,,,2\n"
    ).encode()
    with pytest.raises(ValueError, match="noncontiguous_match_point_rows"):
        list(iter_point_documents(body))
    with pytest.raises(ValueError, match="unexpected_csv_header"):
        parse_match_metadata(b"not,a,charting,csv\n", gender="w")
