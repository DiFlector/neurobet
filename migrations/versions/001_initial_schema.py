"""initial schema with 28 tables, timescale hypertables, and tennis seed data

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-10-08 19:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure timescaledb extension exists
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto CASCADE;")

    # 1. sports
    op.create_table(
        "sports",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 2. leagues
    op.create_table(
        "leagues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sport_code", sa.String(32), sa.ForeignKey("sports.code", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), server_default="fonbet", nullable=False),
        sa.Column("source_league_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("country", sa.String(128), nullable=True),
        sa.Column("surface", sa.String(64), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_leagues_source", "leagues", ["source", "source_league_id"])
    op.create_index("idx_leagues_sport", "leagues", ["sport_code"])

    # 3. participants
    op.create_table(
        "participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sport_code", sa.String(32), sa.ForeignKey("sports.code", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("country", sa.String(128), nullable=True),
        sa.Column("is_team", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_participants_sport_name", "participants", ["sport_code", "canonical_name"])

    # 4. participant_aliases
    op.create_table(
        "participant_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), server_default="fonbet", nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_participant_aliases_lookup", "participant_aliases", ["source", "alias"])

    # 5. events
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(32), server_default="fonbet", nullable=False),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("sport_code", sa.String(32), sa.ForeignKey("sports.code", ondelete="CASCADE"), nullable=False),
        sa.Column("league_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leagues.id", ondelete="SET NULL"), nullable=True),
        sa.Column("participant_a_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("participant_a_name", sa.String(255), nullable=False),
        sa.Column("participant_b_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("participant_b_name", sa.String(255), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), server_default="prematch", nullable=False),
        sa.Column("is_live", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("current_period", sa.Integer(), nullable=True),
        sa.Column("current_score", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_events_source_unique", "events", ["source", "source_event_id"], unique=True)
    op.create_index("idx_events_sport_status", "events", ["sport_code", "status"])
    op.create_index("idx_events_scheduled_start", "events", ["scheduled_start_at"])

    # 6. event_state_snapshots (Timescale Hypertable)
    op.create_table(
        "event_state_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("match_clock_seconds", sa.Integer(), nullable=True),
        sa.Column("current_period", sa.Integer(), nullable=True),
        sa.Column("score", sa.String(64), nullable=True),
        sa.Column("score_detail", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("sport_state", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", "observed_at"),
    )
    op.create_index("idx_event_state_snapshots_lookup", "event_state_snapshots", ["event_id", "observed_at"])
    op.create_index("idx_event_state_snapshots_sport", "event_state_snapshots", ["sport_code", "observed_at"])
    op.execute("SELECT create_hypertable('event_state_snapshots', 'observed_at', if_not_exists => TRUE);")

    # 7. raw_snapshots
    op.create_table(
        "raw_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(32), server_default="fonbet", nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sport_code", sa.String(32), nullable=False),
        sa.Column("page_type", sa.String(32), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("collector_version", sa.String(32), server_default="1.0.0", nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("idx_raw_snapshots_hash", "raw_snapshots", ["content_hash"])
    op.create_index("idx_raw_snapshots_time", "raw_snapshots", ["collected_at"])

    # 8. markets
    op.create_table(
        "markets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_market_id", sa.String(64), nullable=False),
        sa.Column("market_type", sa.String(64), server_default="match_winner", nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("line_value", sa.Numeric(8, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_markets_event_type", "markets", ["event_id", "market_type"])

    # 9. market_selections
    op.create_table(
        "market_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_selection_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_selections_market_outcome", "market_selections", ["market_id", "outcome"])

    # 10. odds_snapshots (Timescale Hypertable)
    op.create_table(
        "odds_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market_type", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("line_value", sa.Numeric(8, 2), nullable=True),
        sa.Column("odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("probability_implied", sa.Numeric(6, 4), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("is_live", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", "observed_at"),
    )
    op.create_index("idx_odds_snapshots_event_time", "odds_snapshots", ["event_id", "observed_at"])
    op.create_index("idx_odds_snapshots_market_lookup", "odds_snapshots", ["event_id", "market_type", "observed_at"])
    op.create_index("idx_odds_snapshots_selection", "odds_snapshots", ["selection_id", "observed_at"])
    op.execute("SELECT create_hypertable('odds_snapshots', 'observed_at', if_not_exists => TRUE);")

    # 11. odds_change_events
    op.create_table(
        "odds_change_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("market_selections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("new_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("delta", sa.Numeric(10, 4), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_odds_changes_event_time", "odds_change_events", ["event_id", "changed_at"])

    # 12. feature_snapshots (Timescale Hypertable)
    op.create_table(
        "feature_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("feature_version", sa.String(32), server_default="1.0.0", nullable=False),
        sa.Column("features", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", "observed_at"),
    )
    op.create_index("idx_feature_snapshots_event_time", "feature_snapshots", ["event_id", "observed_at"])
    op.execute("SELECT create_hypertable('feature_snapshots', 'observed_at', if_not_exists => TRUE);")

    # 13. ml_predictions
    op.create_table(
        "ml_predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("market", sa.String(64), server_default="match_winner", nullable=False),
        sa.Column("predicted_outcome", sa.String(64), nullable=False),
        sa.Column("model_probability", sa.Numeric(6, 4), nullable=False),
        sa.Column("bookmaker_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("fair_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("edge", sa.Numeric(8, 4), nullable=False),
        sa.Column("expected_value", sa.Numeric(8, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=False),
        sa.Column("has_positive_edge", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("outcomes_detail", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_ml_predictions_event", "ml_predictions", ["event_id", "created_at"])
    op.create_index("idx_ml_predictions_edge", "ml_predictions", ["has_positive_edge", "created_at"])

    # 14. llm_decisions
    op.create_table(
        "llm_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("llm_version", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(32), server_default="NEUTRAL", nullable=False),
        sa.Column("confidence_adjustment", sa.Numeric(6, 4), server_default="0.0", nullable=False),
        sa.Column("injury_risk", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("fatigue_risk", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("reasoning", sa.String(1024), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_llm_decisions_event", "llm_decisions", ["event_id", "created_at"])

    # 15. web_research_runs
    op.create_table(
        "web_research_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), server_default="completed", nullable=False),
        sa.Column("documents_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_research_runs_event", "web_research_runs", ["event_id", "created_at"])

    # 16. web_documents
    op.create_table(
        "web_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("web_research_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_domain", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("raw_storage_path", sa.String(512), nullable=True),
    )
    op.create_index("idx_web_documents_hash", "web_documents", ["content_hash"])

    # 17. web_evidence
    op.create_table(
        "web_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("web_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snippet", sa.String(2048), nullable=False),
        sa.Column("relevance_score", sa.Numeric(6, 4), server_default="1.0", nullable=False),
        sa.Column("freshness_score", sa.Numeric(6, 4), server_default="1.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_web_evidence_event", "web_evidence", ["event_id", "created_at"])

    # 18. virtual_accounts
    op.create_table(
        "virtual_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("currency", sa.String(8), server_default="RUB", nullable=False),
        sa.Column("initial_balance", sa.Numeric(12, 2), server_default="100000.00", nullable=False),
        sa.Column("balance", sa.Numeric(12, 2), server_default="100000.00", nullable=False),
        sa.Column("locked_exposure", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 19. ledger_entries (Immutable Financial Journal)
    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("virtual_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bet_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_ledger_entries_account_time", "ledger_entries", ["account_id", "created_at"])

    # 20. bet_proposals
    op.create_table(
        "bet_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("market", sa.String(64), server_default="match_winner", nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("selection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bookmaker_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("fair_odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("model_probability", sa.Numeric(6, 4), nullable=False),
        sa.Column("edge", sa.Numeric(8, 4), nullable=False),
        sa.Column("suggested_stake", sa.Numeric(12, 2), nullable=False),
        sa.Column("ml_prediction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ml_predictions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("llm_decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_decisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_bet_proposals_event", "bet_proposals", ["event_id", "created_at"])

    # 21. bet_validation_results
    op.create_table(
        "bet_validation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bet_proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_accepted", sa.Boolean(), nullable=False),
        sa.Column("rejection_code", sa.String(64), nullable=True),
        sa.Column("checks_passed", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("checks_failed", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_bet_validation_proposal", "bet_validation_results", ["proposal_id"])

    # 22. bets
    op.create_table(
        "bets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("virtual_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bet_proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("market", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("odds", sa.Numeric(10, 4), nullable=False),
        sa.Column("stake", sa.Numeric(12, 2), nullable=False),
        sa.Column("potential_payout", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), server_default="PENDING", nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_bets_account_status", "bets", ["account_id", "status"])
    op.create_index("idx_bets_event", "bets", ["event_id"])

    # 23. bet_settlements
    op.create_table(
        "bet_settlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bet_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bets.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payout", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("net_profit", sa.Numeric(12, 2), nullable=False),
        sa.Column("settlement_reason", sa.String(255), server_default="MATCH_COMPLETED", nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 24. model_versions
    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("version_tag", sa.String(64), unique=True, nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("market", sa.String(64), server_default="match_winner", nullable=False),
        sa.Column("artifact_path", sa.String(512), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("hyperparameters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(32), server_default="ACTIVE", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 25. training_datasets
    op.create_table(
        "training_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("date_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 26. training_runs
    op.create_table(
        "training_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_datasets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("status", sa.String(32), server_default="completed", nullable=False),
        sa.Column("metrics_train", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("metrics_val", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 27. experiment_results
    op.create_table(
        "experiment_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("strategy_config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("backtest_pnl", sa.Numeric(12, 2), nullable=False),
        sa.Column("backtest_roi", sa.Numeric(8, 4), nullable=False),
        sa.Column("win_rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("max_drawdown", sa.Numeric(6, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 28. audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), server_default="system", nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_audit_log_action_time", "audit_log", ["action", "created_at"])

    # =========================================================================
    # SEED DATA: Sport #1 Tennis & Virtual Paper Trading Account
    # =========================================================================
    op.execute("""
        INSERT INTO sports (code, name, status, is_primary, metadata_json, created_at, updated_at)
        VALUES ('tennis', 'Теннис', 'active', TRUE, '{"surface_types": ["hard", "clay", "grass", "indoor"], "hierarchy": ["match", "set", "game", "point"]}'::jsonb, NOW(), NOW())
        ON CONFLICT (code) DO NOTHING;
    """)

    op.execute("""
        INSERT INTO virtual_accounts (id, name, currency, initial_balance, balance, locked_exposure, is_active, created_at, updated_at)
        VALUES ('a0000000-0000-0000-0000-000000000001', 'paper_trading_primary', 'RUB', 100000.00, 100000.00, 0.00, TRUE, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING;
    """)

    op.execute("""
        INSERT INTO ledger_entries (id, account_id, entry_type, amount, balance_after, description, created_at)
        VALUES (
            'b0000000-0000-0000-0000-000000000001',
            'a0000000-0000-0000-0000-000000000001',
            'INITIAL_DEPOSIT',
            100000.00,
            100000.00,
            'Initial Paper Trading Virtual Bankroll Deposit',
            NOW()
        )
        ON CONFLICT (id) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("experiment_results")
    op.drop_table("training_runs")
    op.drop_table("training_datasets")
    op.drop_table("model_versions")
    op.drop_table("bet_settlements")
    op.drop_table("bets")
    op.drop_table("bet_validation_results")
    op.drop_table("bet_proposals")
    op.drop_table("ledger_entries")
    op.drop_table("virtual_accounts")
    op.drop_table("web_evidence")
    op.drop_table("web_documents")
    op.drop_table("web_research_runs")
    op.drop_table("llm_decisions")
    op.drop_table("ml_predictions")
    op.drop_table("feature_snapshots")
    op.drop_table("odds_change_events")
    op.drop_table("odds_snapshots")
    op.drop_table("market_selections")
    op.drop_table("markets")
    op.drop_table("raw_snapshots")
    op.drop_table("event_state_snapshots")
    op.drop_table("events")
    op.drop_table("participant_aliases")
    op.drop_table("participants")
    op.drop_table("leagues")
    op.drop_table("sports")
