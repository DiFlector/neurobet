"""Add append-only validity corrections for parsed tennis result facts."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023_tennis_result_fact_validity"
down_revision: Union[str, None] = "022_tennis_player_identity_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tennis_player_result_facts",
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Historical profile parsers also accepted scored doubles and some two-set
    # exits at men's majors as completed match outcomes. Preserve those old
    # versions, then append an invalidating version at migration time so as-of
    # reads before this correction remain reproducible.
    op.execute(sa.text("""
        WITH latest AS (
            SELECT DISTINCT ON (f.participant_id, f.source, f.source_match_id) f.*,
                   m.winner_name, m.loser_name, m.tournament, m.gender, m.score
            FROM tennis_player_result_facts f
            JOIN ext_tennis_matches m
              ON m.source = 'flashscore'
             AND m.source_match_id = f.source_payload->>'source_match_id'
            WHERE f.source = 'flashscore'
            ORDER BY f.participant_id, f.source, f.source_match_id, f.available_at DESC
        ), bad AS (
            SELECT * FROM latest
            WHERE winner_name LIKE '%/%' OR loser_name LIKE '%/%'
               OR (
                    gender = 'unknown'
                    AND (tournament ILIKE '%Australian Open%'
                      OR tournament ILIKE '%Roland Garros%'
                      OR tournament ILIKE '%French Open%'
                      OR tournament ILIKE '%Wimbledon%'
                      OR tournament ILIKE '%US Open%')
                    AND (SELECT count(*) FROM regexp_matches(score, '[0-9]+-[0-9]+', 'g')) < 3
               )
        )
        INSERT INTO tennis_player_result_facts (
            id, participant_id, source, source_match_id, result_hash,
            played_at, available_at, surface, tour_level, won, valid,
            sets_played, duration_minutes, serve_points, serve_points_won,
            source_payload, created_at
        )
        SELECT gen_random_uuid(), participant_id, source, source_match_id,
               md5('invalidated:' || result_hash), played_at, now(), surface,
               tour_level, won, false, sets_played, duration_minutes,
               serve_points, serve_points_won,
               source_payload || jsonb_build_object(
                   'invalidated_reason', CASE
                       WHEN winner_name LIKE '%/%' OR loser_name LIKE '%/%'
                           THEN 'doubles_excluded_from_singles'
                       ELSE 'male_major_score_does_not_prove_match_winner'
                   END,
                   'corrected_at', now(),
                   'parser_version', 'flashscore-tennis-player-v7'
               ), now()
        FROM bad
        WHERE valid IS DISTINCT FROM false
        ON CONFLICT (participant_id, source, source_match_id, available_at) DO NOTHING
    """))


def downgrade() -> None:
    op.drop_column("tennis_player_result_facts", "valid")

