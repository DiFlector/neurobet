from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from tennis_adapter.history_linking import normalize_api_pbp, official_result
from app.point_history import _orient_alternative_point_events


def _game(**updates):
    return {"set_number": "Set 1", "number_game": "1", "score": "1 - 0",
            "serve_winner": "First Player", "player_served": "First Player",
            "points": [{"number_point": str(i), "score": score} for i, score in
                       enumerate(("15 - 0", "30 - 0", "40 - 0"), 1)], **updates}


def test_api_coordinates_and_separate_game_result_do_not_invent_terminal_point():
    rows, quality = normalize_api_pbp({"pointbypoint": [_game()]})
    assert quality['confirmed_points'] == 3 and quality['confirmed_games'] == 1
    assert quality['complete'] is False
    assert len(rows) == 4 and rows[-1]['event_kind'] == 'game_result'
    assert all(row['game_score_before'] == [0, 0] and row['game_score'] == [1, 0] for row in rows)
    assert rows[-1]['state_before'] == {'game_score': [0, 0]}


def test_point_gap_breaks_continuity_and_later_observed_pair_is_usable():
    rows, quality = normalize_api_pbp({'pointbypoint': [_game(points=[
        {'number_point': '1', 'score': '15 - 0'}, {'number_point': '3', 'score': '40 - 0'},
        {'number_point': '4', 'score': '40 - 15'},
    ])]})
    assert [row['confirmed'] for row in rows[:3]] == [True, False, True]
    assert rows[1]['state_before'] is None
    assert rows[2]['state_before'] == ['40', '0'] and rows[2]['winner'] == 1


def test_source_advantage_is_regular_score_not_tiebreak():
    rows, _ = normalize_api_pbp({'pointbypoint': [_game(score='0 - 1', serve_winner='Second Player', points=[
        {'number_point': '6', 'score': '40 - 40'}, {'number_point': '7', 'score': '40 - A'},
        {'number_point': '8', 'score': '40 - 40'},
    ])]})
    assert [row['winner'] for row in rows[:3]] == [None, 1, 0]
    assert not any(row['tie_break'] for row in rows)


def test_game_only_history_and_unknown_game_winner_are_distinguished():
    rows, quality = normalize_api_pbp({'pointbypoint': [_game(points=[])]})
    assert len(rows) == 1 and quality['confirmed_points'] == 0 and quality['confirmed_games'] == 1
    rows, quality = normalize_api_pbp({'pointbypoint': [_game(serve_winner=None)]})
    assert not rows and quality['gaps'][0]['reason'] == 'game_coordinates_or_winner_unknown'


def test_conflicting_game_result_and_serving_evidence_is_excluded():
    rows, quality = normalize_api_pbp({'pointbypoint': [_game(serve_lost='First Player')]})
    assert not rows and quality['gaps'][0]['reason'] == 'game_result_conflict'
    rows, _ = normalize_api_pbp({'pointbypoint': [_game(score='3 - 2')]})
    assert not rows


def test_tiebreak_does_not_reuse_game_server_for_every_point():
    rows, _ = normalize_api_pbp({'pointbypoint': [_game(number_game='13', score='7 - 6', points=[
        {'number_point': '1', 'score': '1 - 0'}, {'number_point': '2', 'score': '1 - 1'},
    ])]})
    assert rows[0]['game_score_before'] == [6, 6]
    assert [row['winner'] for row in rows[:2]] == [0, 1]
    assert all(row['server'] is None for row in rows[:2])


def test_reverse_orientation_flips_nested_game_coordinates_without_mutation():
    rows, _ = normalize_api_pbp({'pointbypoint': [_game()]})
    flipped = _orient_alternative_point_events(rows, source_first_is_fonbet_a=False)
    assert flipped[0]['winner'] == 1 and flipped[0]['state_after'] == ['0', '15']
    assert flipped[-1]['state_after'] == {'game_result': [0, 1]}
    assert flipped[-1]['game_score'] == [0, 1] and flipped[-1]['winner'] == 1
    assert rows[-1]['state_after'] == {'game_result': [1, 0]}


def test_official_linking_rejects_retirement_and_conflicting_set_result():
    meta={'fonbet_official_result': {'status':'FINISHED','winner':'player_a','final_score':'2:0 (6-4, 6-3)'}}
    assert official_result('finished',meta) == (0,[(6,4),(6,3)],'confirmed')
    meta['fonbet_official_result']['is_retired']=True
    assert official_result('finished',meta)[2]=='walkover_or_retirement'


def test_database_link_plan_is_oriented_append_only_idempotent_and_proof_checked():
    from db.connection import SessionLocal
    from db.models import Event, Participant, TennisPlayerIdentityLink, TennisPlayerProfile
    from db.models.external_tennis import (ExtTennisCanonicalPlayer, ExtTennisCanonicalPlayerAlias,
        ExtTennisCanonicalMatch, ExtTennisMatch, ExtTennisMatchObservation)
    from app.tennis_history_linking import plan_api_link, append_api_link
    now=datetime.now(timezone.utc); earlier=now-timedelta(hours=1)
    a,b,ca,cb,eid=[uuid.uuid4() for _ in range(5)]
    suffix=uuid.uuid4().hex; mid='__link_v2_'+suffix
    with SessionLocal() as session:
        try:
            session.add_all([Participant(id=pid,sport_code='tennis',canonical_name=name,is_team=False)
                             for pid,name in ((a,'Fixture A'),(b,'Fixture B'))])
            session.add_all([ExtTennisCanonicalPlayer(id=pid,display_name=name,gender='m',surname_key=name,
                             initial=name[0],source_ids={}) for pid,name in ((ca,'FixtureA'),(cb,'FixtureB'))])
            session.flush()
            event=Event(id=eid,source='fonbet',source_event_id=str(uuid.uuid4().int)[:18],sport_code='tennis',
                participant_a_id=a,participant_b_id=b,participant_a_name='Fixture A',participant_b_name='Fixture B',
                scheduled_start_at=earlier,finished_at=earlier,status='finished',is_live=False,
                current_score={},metadata_json={'fonbet_official_result':{'status':'FINISHED','winner':'player_a',
                                                                       'final_score':'2:0 (6-4, 6-3)'}})
            session.add(event)
            for participant,canonical,identity in ((a,ca,'fa'+suffix),(b,cb,'fb'+suffix)):
                session.add(TennisPlayerProfile(participant_id=participant))
                session.add(TennisPlayerIdentityLink(participant_id=participant,source='flashscore',
                    source_player_id=identity,status='confirmed',available_at=earlier))
                session.add(ExtTennisCanonicalPlayerAlias(source='flashscore',source_player_id=identity,
                    canonical_player_id=canonical,status='confirmed',created_at=earlier))
            pa,pb='api-a'+suffix,'api-b'+suffix
            aliases=[]
            for canonical,provider_id in ((ca,pa),(cb,pb)):
                alias=ExtTennisCanonicalPlayerAlias(source='api_tennis',source_player_id=provider_id,
                    canonical_player_id=canonical,status='confirmed',created_at=earlier)
                aliases.append(alias);session.add(alias)
            session.add(ExtTennisCanonicalMatch(id=uuid.uuid4(),canonical_key=mid,played_at=earlier,available_at=earlier,
                winner_name='Fixture A',loser_name='Fixture B',winner_player_id=ca,loser_player_id=cb,
                tournament='Fixture Open',stats={'sources':['api_tennis:'+mid]}))
            session.add(ExtTennisMatch(source='api_tennis',source_match_id=mid,played_at=earlier,
                available_at=earlier,winner_name='Fixture A',loser_name='Fixture B',winner_player_id=pa,loser_player_id=pb))
            raw={'first_player_key':pb,'second_player_key':pa,'event_status':'Finished','event_winner':'Second Player',
                'scores':[{'score_set':'1','score_first':'4','score_second':'6'},
                          {'score_set':'2','score_first':'3','score_second':'6'}],
                'pointbypoint':[_game(score='0 - 1',serve_winner='Second Player',points=[
                    {'number_point':'1','score':'0 - 15'}])]}
            session.add(ExtTennisMatchObservation(source='api_tennis',source_match_id=mid,available_at=earlier,
                observation_hash=suffix,source_url='https://api-tennis.invalid/fixture',parser_version='fixture',payload={'event':raw}))
            session.flush()
            plan,reason=plan_api_link(session,event,as_of=now)
            assert reason=='ready' and plan['events'][0]['winner']==0
            assert plan['events'][0]['server']==1
            assert plan['events'][-1]['state_after']=={'game_result':[1,0]}
            version,status=append_api_link(session,plan)
            assert status=='appended' and version.available_at>=now
            repeated,status=append_api_link(session,plan)
            assert status=='existing' and repeated.id==version.id
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'research'))
            from tennis_label_audit import _direct_api_context, _load_direct_api_histories
            from db.models import TennisPointDocument
            document=session.get(TennisPointDocument,version.document_id)
            assert _direct_api_context(version,document,event,version.available_at-timedelta(microseconds=1)) is None
            assert _direct_api_context(version,document,event,version.available_at)['reference']=='api_tennis:'+mid
            assert len(_load_direct_api_histories(session,{eid:event},version.available_at))==1
            aliases[0].status='rejected';session.flush()
            assert plan_api_link(session,event,as_of=datetime.now(timezone.utc))[1]=='provider_player_ids_unconfirmed_or_conflicting'
            assert not _load_direct_api_histories(session,{eid:event},datetime.now(timezone.utc))
        finally:
            session.rollback()


def test_cross_source_conflicts_are_removed_and_agreement_is_not_doubled():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'/'research'))
    from tennis_label_audit import _resolve_source_examples
    a={'target':'point','fonbet_event_id':'e1','snapshot_id':'s1','snapshot_observed_at':'2026-10-28T01:00:00Z',
       'target_state':[1,[0,0],[1,0],0,False],'label':0,'source':'flashscore',
       'label_available_at':'2026-10-28T02:00:00+00:00'}
    b={**a,'source':'api_tennis','label':1,'label_available_at':'2026-10-28T03:00:00+00:00'}
    kept,conflicts=_resolve_source_examples({('point',('e1','fs',0)):a,('point',('e1','api',1)):b})
    assert not kept and len(conflicts)==1
    b['label']=0
    kept,conflicts=_resolve_source_examples({('point',('e1','fs',0)):a,('point',('e1','api',0)):b})
    assert not conflicts and len(kept)==1
    row=next(iter(kept.values()))
    assert row['source']=='flashscore' and row['supporting_sources']==['api_tennis','flashscore']
