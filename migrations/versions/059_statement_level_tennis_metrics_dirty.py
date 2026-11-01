"""Coalesce independent-metric dirty triggers per SQL statement.

Row-level triggers updated one shared generation row once for every profile or
fact row. Concurrent batch imports could interleave those updates with their
own row locks and deadlock. The dirtiness signal is transaction-level, so one
increment per changed table statement is sufficient.
"""

from alembic import op

revision = "059_statement_metric_dirty"
down_revision = "058_tennis_state_raw_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in (
        "ext_tennis_canonical_matches",
        "ext_tennis_canonical_player_aliases",
        "tennis_player_profiles",
        "tennis_player_identity_links",
        "tennis_player_result_facts",
    ):
        trigger = f"trg_{table}_independent_metrics_dirty"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(f"""
          CREATE TRIGGER {trigger}
          AFTER INSERT OR UPDATE OR DELETE ON {table}
          FOR EACH STATEMENT EXECUTE FUNCTION mark_tennis_independent_metrics_dirty()
        """)


def downgrade() -> None:
    for table in (
        "ext_tennis_canonical_matches",
        "ext_tennis_canonical_player_aliases",
        "tennis_player_profiles",
        "tennis_player_identity_links",
        "tennis_player_result_facts",
    ):
        trigger = f"trg_{table}_independent_metrics_dirty"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(f"""
          CREATE TRIGGER {trigger}
          AFTER INSERT OR UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION mark_tennis_independent_metrics_dirty()
        """)
