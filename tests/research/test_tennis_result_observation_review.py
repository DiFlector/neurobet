from tennis_result_observation import score_signature


def test_score_signature_normalizes_punctuation_without_changing_score_digits():
    assert score_signature("0:2 (4-6 6-7)") == score_signature("0-2 (4-6, 6-7)")
    assert score_signature("2:1 (6-4 4-6 6-3)") != score_signature("1:2 (4-6 6-4 3-6)")


def test_score_signature_rejects_partial_scores():
    assert score_signature("1-2") is None
    assert score_signature("6-4 4-6") is None
