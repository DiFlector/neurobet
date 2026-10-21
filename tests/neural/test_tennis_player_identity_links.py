from types import SimpleNamespace

from app.player_metrics import _latest_confirmed_flashscore_id, _profile_identity_matches


def test_newer_revocation_does_not_resurrect_old_flashscore_confirmation():
    rows = [
        SimpleNamespace(source_player_id="player-1", status="revoked"),
        SimpleNamespace(source_player_id="player-1", status="confirmed"),
    ]

    assert _latest_confirmed_flashscore_id(rows) is None


def test_latest_manual_confirmation_is_selected():
    rows = [
        SimpleNamespace(source_player_id="player-2", status="confirmed"),
        SimpleNamespace(source_player_id="player-1", status="revoked"),
    ]

    assert _latest_confirmed_flashscore_id(rows) == "player-2"


def test_cached_profile_with_conflicting_source_identity_is_not_reused():
    profile = SimpleNamespace(source_identities={"flashscore": "old-player"})
    assert not _profile_identity_matches(profile, "confirmed-player")
    assert _profile_identity_matches(profile, "old-player")
