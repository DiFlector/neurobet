from reparse_tennis_point_documents import _is_adjacent_duplicate


def test_reparse_suppresses_only_adjacent_identical_documents_and_keeps_a_b_a():
    hashes = ["A", "A", "B", "B", "A"]
    previous = None
    suppressed = []
    for current in hashes:
        suppressed.append(_is_adjacent_duplicate(previous, current))
        previous = current
    assert suppressed == [False, True, False, True, False]
