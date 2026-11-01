"""Track source changes that require a causal tennis metrics replay."""

from alembic import op

revision = "049_tennis_ind_refresh"
down_revision = "048_tennis_pit_immutable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE tennis_independent_metric_refresh_state (
        id smallint PRIMARY KEY CHECK (id = 1),
        generation bigint NOT NULL DEFAULT 1,
        processed_generation bigint NOT NULL DEFAULT 0,
        updated_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("INSERT INTO tennis_independent_metric_refresh_state(id) VALUES (1)")
    op.execute("""
      CREATE FUNCTION mark_tennis_independent_metrics_dirty() RETURNS trigger AS $$
      BEGIN
        UPDATE tennis_independent_metric_refresh_state
        SET generation = generation + 1, updated_at = now()
        WHERE id = 1;
        RETURN NULL;
      END;
      $$ LANGUAGE plpgsql
    """)
    for table in (
        "ext_tennis_canonical_matches",
        "ext_tennis_canonical_player_aliases",
        "tennis_player_profiles",
        "tennis_player_identity_links",
    ):
        op.execute(f"""
          CREATE TRIGGER trg_{table}_independent_metrics_dirty
          AFTER INSERT OR UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION mark_tennis_independent_metrics_dirty()
        """)


def downgrade() -> None:
    for table in (
        "ext_tennis_canonical_matches",
        "ext_tennis_canonical_player_aliases",
        "tennis_player_profiles",
        "tennis_player_identity_links",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_independent_metrics_dirty ON {table}")
    op.execute("DROP FUNCTION IF EXISTS mark_tennis_independent_metrics_dirty()")
    op.drop_table("tennis_independent_metric_refresh_state")
