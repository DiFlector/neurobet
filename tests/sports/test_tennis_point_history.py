from tennis_adapter.point_history import (
    is_authoritative_point_history_version,
    parse_point_history_html,
)


def test_unrecognized_or_unavailable_empty_document_does_not_tombstone_a_set():
    assert not is_authoritative_point_history_version([], {
        "set_number": 1,
        "set_tab_confirmed": False,
        "gaps": ["point_by_point_markup_missing", "HISTORY_EMPTY_OR_UNRECOGNIZED"],
    })
    assert not is_authoritative_point_history_version([], {
        "set_number": 1,
        "fetch_status": "history_selector_missing",
    })


def test_explicit_empty_correction_tombstones_but_unmarked_empty_does_not():
    assert not is_authoritative_point_history_version([], {"set_number": 2, "complete": False})
    assert is_authoritative_point_history_version([], {
        "set_number": 2,
        "explicit_empty_correction": True,
    })


def test_parses_normal_game_server_and_confirmed_score_transitions():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Point by point - Set 1</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__servis matchHistoryRow__highlight matchHistoryRow__home"></div>
        <div class="matchHistoryRow__servis matchHistoryRow__away"></div>
        <div class="matchHistoryRow__scoreBox"><span>1</span><span>0</span></div></div>
      <div class="matchHistoryRow__fifteens"><div class="matchHistoryRow__fifteen">15:0,</div>
        <div class="matchHistoryRow__fifteen">30:0,</div><div class="matchHistoryRow__fifteen">40:0,</div>
        <div class="matchHistoryRow__gameResult"><span>GAME</span><span>1</span>-<span>0</span></div></div>
    </div>'''
    parsed = parse_point_history_html(html)
    assert parsed["quality"]["confirmed_points"] == 4
    assert [e["winner"] for e in parsed["events"]] == [0, 0, 0, 0]
    assert all(e["server"] == 0 for e in parsed["events"])


def test_server_uses_the_unique_highlighted_marker_not_the_last_placeholder():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Point by point - Set 1</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__servis matchHistoryRow__home"></div>
        <div class="matchHistoryRow__servis matchHistoryRow__highlight matchHistoryRow__away"></div>
        <div class="matchHistoryRow__scoreBox"><span>0</span><span>1</span></div></div>
      <div class="matchHistoryRow__fifteens"><div class="matchHistoryRow__fifteen">0:15,</div>
        <div class="matchHistoryRow__gameResult">GAME 0-1</div></div>
    </div>'''
    parsed = parse_point_history_html(html)
    assert parsed["events"]
    assert all(event["server"] == 1 for event in parsed["events"])


def test_deuce_and_advantage_score_changes_are_confirmed():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Point by point - Set 1</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox"><span>1</span><span>0</span></div></div>
      <div class="matchHistoryRow__fifteens"><div class="matchHistoryRow__fifteen">40:40,</div>
      <div class="matchHistoryRow__fifteen">40:A,</div><div class="matchHistoryRow__fifteen">40:40,</div>
      <div class="matchHistoryRow__fifteen">A:40,</div><div class="matchHistoryRow__gameResult">GAME 1-0</div></div>
    </div>'''
    parsed = parse_point_history_html(html)
    assert [e["winner"] for e in parsed["events"][:4]] == [None, 1, 0, 0]
    assert parsed["events"][-1]["winner"] == 0


def test_terminal_game_point_requires_matching_game_score_increment():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Point by point - Set 1</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox"><span>0</span><span>1</span></div></div>
      <div class="matchHistoryRow__fifteens"><div class="matchHistoryRow__fifteen">40:0,</div>
        <div class="matchHistoryRow__gameResult">GAME 0-1</div></div>
    </div>'''
    parsed = parse_point_history_html(html)
    terminal = [event for event in parsed["events"] if event["quality"] == "terminal_from_game_result"]
    assert terminal == []
    assert any(gap["reason"] == "ambiguous_terminal_point" for gap in parsed["quality"]["gaps"])


def test_tiebreak_winner_marker_must_agree_with_one_point_transition():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Tie-break - Set 1</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox">
        <span data-testid="wcl-pointByPointScore" data-winning="true">1</span>
        <span data-testid="wcl-pointByPointScore" data-winning="false">0</span></div></div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox">
        <span data-testid="wcl-pointByPointScore" data-winning="true">1</span>
        <span data-testid="wcl-pointByPointScore" data-winning="false">1</span></div></div>
    </div>'''
    parsed = parse_point_history_html(html)
    assert [event["confirmed"] for event in parsed["events"]] == [True, False]
    assert parsed["events"][1]["winner"] is None


def test_tiebreak_numeric_score_and_gaps_are_explicit():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Tie-break - Set 3</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__servis matchHistoryRow__away"></div>
        <div class="matchHistoryRow__scoreBox"><span data-testid="wcl-pointByPointScore" data-winning="true">1</span>
          <span data-testid="wcl-pointByPointScore" data-winning="false">0</span></div></div></div>'''
    parsed = parse_point_history_html(html)
    assert parsed["events"][0]["tie_break"] is True
    assert parsed["events"][0]["point_score"] == [1, 0]
    assert parsed["events"][0]["winner"] == 0


def test_completed_tiebreak_emits_set_and_game_terminal_marker():
    scores = [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0),
              (6, 1), (6, 2), (6, 3), (6, 4), (6, 5), (7, 5)]
    rows = []
    before = (0, 0)
    for a, b in scores:
        winner = 0 if a > before[0] else 1
        rows.append(f'''<div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox">
          <span data-testid="wcl-pointByPointScore" data-winning="{'true' if winner == 0 else 'false'}">{a}</span>
          <span data-testid="wcl-pointByPointScore" data-winning="{'true' if winner == 1 else 'false'}">{b}</span>
        </div></div>''')
        before = (a, b)
    parsed = parse_point_history_html(
        '<div class="matchHistoryRowWrapper"><div class="sectionHeader">Tie-break - Set 1</div>'
        + ''.join(rows) + '</div>'
    )
    terminal = [event for event in parsed["events"]
                if event["quality"] == "terminal_from_tiebreak_result"]
    assert len(terminal) == 1
    assert terminal[0]["winner"] == 0
    assert terminal[0]["game_score"] == [7, 6]
    assert terminal[0]["state_before"] == [7, 5]
    assert parsed["quality"]["confirmed_points"] == len(scores)


def test_russian_flashscore_set_and_tiebreak_headers_are_understood():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Очко за очком - 1-й сет</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__servis matchHistoryRow__home"></div>
        <div class="matchHistoryRow__scoreBox"><span>1</span><span>0</span></div></div>
      <div class="matchHistoryRow__fifteens"><div class="matchHistoryRow__fifteen">15:0,</div>
        <div class="matchHistoryRow__fifteen">30:0,</div><div class="matchHistoryRow__fifteen">40:0,</div>
        <div class="matchHistoryRow__gameResult">GAME 1-0</div></div>
    </div>
    <div class="matchHistoryRowWrapper"><div class="sectionHeader">Тай-брейк - 1-й сет</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox">
        <span data-testid="wcl-pointByPointScore" data-winning="true">1</span>
        <span data-testid="wcl-pointByPointScore" data-winning="false">0</span></div></div>
    </div>'''
    parsed = parse_point_history_html(html)
    assert parsed["sets"] == [1]
    assert all(event["set"] == 1 for event in parsed["events"])
    assert parsed["events"][-1]["tie_break"] is True
    assert parsed["events"][-1]["point_score"] == [1, 0]


def test_set_tiebreak_superscript_summary_is_not_misread_as_game_score():
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Очко за очком - 1-й сет</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox">
        <span>6<sup>4</sup></span><span>7<sup>7</sup></span></div></div>
    </div>'''
    parsed = parse_point_history_html(html)
    assert parsed["events"] == []
    assert parsed["quality"]["gaps"] == []

