"""Dirty independent metrics after late serve facts and provider ranking observations."""

from alembic import op

revision = "050_tennis_ind_fact_dirty"
down_revision = "049_tennis_ind_refresh"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TRIGGER trg_tennis_player_result_facts_independent_metrics_dirty
      AFTER INSERT OR UPDATE OR DELETE ON tennis_player_result_facts
      FOR EACH ROW EXECUTE FUNCTION mark_tennis_independent_metrics_dirty()
    """)
    op.execute("""
      CREATE FUNCTION mark_live_tennis_ranking_dirty() RETURNS trigger AS $$
      BEGIN
        IF COALESCE(NEW.source, OLD.source) = 'live_tennis_api'
           AND COALESCE(NEW.state_key, OLD.state_key) LIKE 'profile:%' THEN
          UPDATE tennis_independent_metric_refresh_state
          SET generation = generation + 1, updated_at = now()
          WHERE id = 1;
        END IF;
        RETURN NULL;
      END;
      $$ LANGUAGE plpgsql
    """)
    op.execute("""
      CREATE TRIGGER trg_external_tennis_source_state_ranking_dirty
      AFTER INSERT OR UPDATE OR DELETE ON external_tennis_source_state
      FOR EACH ROW EXECUTE FUNCTION mark_live_tennis_ranking_dirty()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_external_tennis_source_state_ranking_dirty ON external_tennis_source_state")
    op.execute("DROP FUNCTION IF EXISTS mark_live_tennis_ranking_dirty()")
    op.execute("DROP TRIGGER IF EXISTS trg_tennis_player_result_facts_independent_metrics_dirty ON tennis_player_result_facts")
