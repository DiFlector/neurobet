"""initial schema: live + finished schemas, ported from SQLite

Revision ID: 0001
Revises:
Create Date: 2026-08-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS live;")
    op.execute("CREATE SCHEMA IF NOT EXISTS finished;")

    # ------------------------------------------------------------------
    # live schema — mirrors the old autobet.db
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE live.events (
            event_id BIGINT PRIMARY KEY,
            sport_id BIGINT,
            sport_path TEXT,
            match_name TEXT,
            team_1 TEXT,
            team_2 TEXT,
            score_1 INTEGER,
            score_2 INTEGER,
            score TEXT,
            timer TEXT,
            is_live INTEGER DEFAULT 1,
            sub_markets_json TEXT,
            total_odds_count INTEGER DEFAULT 0,
            last_updated_at TEXT,
            miss_count INTEGER DEFAULT 0,
            missing_since TEXT,
            period_scores_json TEXT,
            named_scores_json TEXT
        );
    """)
    op.execute("CREATE INDEX idx_events_live_miss ON live.events (is_live, miss_count);")
    op.execute("CREATE INDEX idx_events_sport ON live.events (sport_path);")

    op.execute("""
        CREATE TABLE live.odds_history (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id BIGINT,
            factor_id BIGINT,
            market_prefix TEXT,
            label TEXT,
            parameter TEXT,
            coefficient DOUBLE PRECISION,
            score_at_time TEXT,
            timestamp TEXT
        );
    """)
    op.execute("CREATE INDEX idx_odds_hist_event ON live.odds_history (event_id);")
    op.execute("CREATE INDEX idx_odds_hist_lookup ON live.odds_history (event_id, factor_id, parameter, market_prefix);")
    op.execute("CREATE INDEX idx_odds_hist_ts ON live.odds_history (timestamp);")

    op.execute("""
        CREATE TABLE live.latest_odds (
            event_id BIGINT,
            factor_id BIGINT,
            market_prefix TEXT,
            label TEXT,
            parameter TEXT,
            coefficient DOUBLE PRECISION,
            initial_coefficient DOUBLE PRECISION,
            score_at_time TEXT,
            updated_at TEXT,
            PRIMARY KEY (event_id, factor_id, parameter, market_prefix)
        );
    """)

    op.execute("""
        CREATE TABLE live.ai_predictions (
            event_id BIGINT,
            factor_id BIGINT,
            market_prefix TEXT,
            parameter TEXT,
            win_probability DOUBLE PRECISION,
            error_rate DOUBLE PRECISION,
            expected_roi DOUBLE PRECISION,
            lightgbm_score DOUBLE PRECISION,
            pytorch_score DOUBLE PRECISION,
            updated_at TEXT,
            PRIMARY KEY (event_id, factor_id, parameter, market_prefix)
        );
    """)

    # ------------------------------------------------------------------
    # finished schema — mirrors the old autobet_finished.db
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE finished.finished_events (
            event_id BIGINT PRIMARY KEY,
            sport_id BIGINT,
            sport_path TEXT,
            match_name TEXT,
            team_1 TEXT,
            team_2 TEXT,
            score_1 INTEGER,
            score_2 INTEGER,
            score TEXT,
            finished_at TEXT,
            archived_count INTEGER DEFAULT 1,
            period_scores_json TEXT,
            named_scores_json TEXT
        );
    """)
    op.execute("CREATE INDEX idx_finished_events_sport ON finished.finished_events (sport_path);")

    # Legacy table, apparently no longer written to anywhere in the app — ported
    # empty for parity in case an unfound codepath still reads it; revisit dropping
    # it later once that's confirmed over time.
    op.execute("""
        CREATE TABLE finished.finished_odds_history (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id BIGINT,
            factor_id BIGINT,
            market_prefix TEXT,
            label TEXT,
            parameter TEXT,
            initial_coefficient DOUBLE PRECISION,
            final_coefficient DOUBLE PRECISION,
            score_at_time TEXT,
            is_win INTEGER DEFAULT 0,
            timestamp TEXT,
            finished_at TEXT
        );
    """)
    op.execute("CREATE INDEX idx_finished_odds_win ON finished.finished_odds_history (event_id, is_win);")

    op.execute("""
        CREATE TABLE finished.finished_bets (
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            event_id BIGINT,
            factor_id BIGINT,
            market_prefix TEXT,
            label TEXT,
            parameter TEXT,
            initial_coefficient DOUBLE PRECISION,
            final_coefficient DOUBLE PRECISION,
            min_coefficient DOUBLE PRECISION,
            max_coefficient DOUBLE PRECISION,
            samples_count INTEGER,
            odds_seq_json TEXT,
            score_at_time TEXT,
            is_win INTEGER,
            first_seen_at TEXT,
            finished_at TEXT,
            predicted_win_probability DOUBLE PRECISION,
            score_seq_json TEXT,
            score_diff_at_bet INTEGER,
            trained_count INTEGER DEFAULT 0,
            is_push INTEGER DEFAULT 0,
            PRIMARY KEY (event_id, factor_id, parameter, market_prefix)
        );
    """)
    # `id` is a surrogate for the frontend/get_neurobets_history pagination cursor —
    # SQLite's implicit rowid had no Postgres equivalent for a composite-PK table.
    op.execute("CREATE UNIQUE INDEX idx_finished_bets_id ON finished.finished_bets (id);")
    op.execute("CREATE INDEX idx_finished_bets_win ON finished.finished_bets (event_id, is_win);")
    op.execute("CREATE INDEX idx_finished_bets_trained ON finished.finished_bets (trained_count, finished_at);")

    op.execute("""
        CREATE TABLE finished.bankroll_accounts (
            account TEXT PRIMARY KEY,
            balance DOUBLE PRECISION NOT NULL,
            start_balance DOUBLE PRECISION NOT NULL,
            peak_balance DOUBLE PRECISION NOT NULL,
            locked DOUBLE PRECISION NOT NULL DEFAULT 0,
            rounds INTEGER NOT NULL DEFAULT 0,
            bets_placed INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            total_staked DOUBLE PRECISION NOT NULL DEFAULT 0,
            total_returned DOUBLE PRECISION NOT NULL DEFAULT 0,
            ruin_count INTEGER NOT NULL DEFAULT 0,
            is_ruined INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );
    """)

    op.execute("""
        CREATE TABLE finished.bankroll_ledger (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            account TEXT NOT NULL,
            round_no INTEGER,
            balance_before DOUBLE PRECISION,
            balance_after DOUBLE PRECISION,
            staked DOUBLE PRECISION,
            returned DOUBLE PRECISION,
            bets_count INTEGER,
            ruined INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        );
    """)
    op.execute("CREATE INDEX idx_bankroll_ledger_account ON finished.bankroll_ledger (account, id);")

    op.execute("""
        CREATE TABLE finished.live_bets (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id BIGINT,
            factor_id BIGINT,
            market_prefix TEXT,
            parameter TEXT,
            label TEXT,
            match_name TEXT,
            coefficient DOUBLE PRECISION,
            stake DOUBLE PRECISION,
            stake_fraction DOUBLE PRECISION,
            win_probability DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'open',
            payout DOUBLE PRECISION,
            placed_at TEXT,
            settled_at TEXT,
            UNIQUE (event_id, factor_id, parameter, market_prefix)
        );
    """)
    op.execute("CREATE INDEX idx_live_bets_status ON finished.live_bets (status);")

    op.execute("""
        INSERT INTO finished.bankroll_accounts (account, balance, start_balance, peak_balance, updated_at)
        VALUES
            ('training', 1000.0, 1000.0, 1000.0, now()::text),
            ('live', 1000.0, 1000.0, 1000.0, now()::text)
        ON CONFLICT (account) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP SCHEMA finished CASCADE;")
    op.execute("DROP SCHEMA live CASCADE;")
