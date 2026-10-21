"""Durable human review queue for Flashscore identities and match links."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "029_manual_confirmations"
down_revision: Union[str, None] = "028_flashscore_search_retries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manual_confirmation_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="flashscore"),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
    )
    op.create_index("idx_manual_confirmation_status_created", "manual_confirmation_requests", ["status", "created_at"])
    op.create_index("idx_manual_confirmation_subject", "manual_confirmation_requests", ["kind", "subject_id", "status"])
    op.create_index(
        "uq_manual_confirmation_pending_pair",
        "manual_confirmation_requests",
        ["kind", "subject_id", "candidate_id", "source"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "manual_confirmation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("manual_confirmation_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_manual_confirmation_decisions_request", "manual_confirmation_decisions", ["request_id", "created_at"])

    # Backfill current unresolved player candidates. Existing confirmed or
    # rejected identities are intentionally left out.
    op.execute(sa.text("""
        INSERT INTO manual_confirmation_requests
            (id, kind, subject_id, candidate_id, source, evidence_json, status, version, created_at, updated_at)
        SELECT gen_random_uuid(), 'player_profile', p.participant_id::text,
               c.value->>'source_player_id', 'flashscore',
               jsonb_build_object('participant_name', COALESCE(part.canonical_name, ''), 'candidate', c.value),
               'pending', 1, now(), now()
        FROM tennis_player_profiles p
        JOIN participants part ON part.id = p.participant_id
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(p.source_identities->'flashscore_candidates', '[]'::jsonb)) c(value)
        WHERE p.sync_status = 'identity_confirmation_required'
          AND COALESCE(c.value->>'source_player_id', '') <> ''
          AND NOT EXISTS (
              SELECT 1 FROM tennis_player_identity_links l
              WHERE l.participant_id = p.participant_id AND l.source = 'flashscore'
                AND l.source_player_id = c.value->>'source_player_id'
                AND l.status IN ('confirmed', 'rejected')
          )
        ON CONFLICT DO NOTHING
    """))

    # Backfill ambiguous live match candidates that have not since been linked.
    op.execute(sa.text("""
        WITH candidates AS (
            SELECT DISTINCT ON ((evidence_json->>'candidate_event_id'), source_match_id)
                   source_match_id, evidence_json->>'candidate_event_id' AS event_id, evidence_json, available_at
            FROM tennis_point_match_links
            WHERE status = 'ambiguous' AND COALESCE(evidence_json->>'candidate_event_id', '') <> ''
            ORDER BY (evidence_json->>'candidate_event_id'), source_match_id, available_at DESC, id DESC
        )
        INSERT INTO manual_confirmation_requests
            (id, kind, subject_id, candidate_id, source, evidence_json, status, version, created_at, updated_at)
        SELECT gen_random_uuid(), 'event_match', c.event_id, c.source_match_id, 'flashscore',
               jsonb_build_object('event_id', c.event_id, 'source_match_id', c.source_match_id, 'evidence', c.evidence_json),
               'pending', 1, c.available_at, c.available_at
        FROM candidates c
        WHERE NOT EXISTS (
            SELECT 1 FROM tennis_point_match_links l
            WHERE l.source_match_id = c.source_match_id
              AND l.fonbet_event_id::text = c.event_id
              AND l.status IN ('matched', 'manual', 'rejected')
              AND l.available_at >= c.available_at
        )
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    op.drop_index("idx_manual_confirmation_decisions_request", table_name="manual_confirmation_decisions")
    op.drop_table("manual_confirmation_decisions")
    op.drop_index("uq_manual_confirmation_pending_pair", table_name="manual_confirmation_requests")
    op.drop_index("idx_manual_confirmation_subject", table_name="manual_confirmation_requests")
    op.drop_index("idx_manual_confirmation_status_created", table_name="manual_confirmation_requests")
    op.drop_table("manual_confirmation_requests")
