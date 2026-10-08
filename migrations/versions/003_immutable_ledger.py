"""immutable ledger and direct balance update protection triggers

Revision ID: 003_immutable_ledger
Revises: 002_data_quality
Create Date: 2026-10-08 21:19:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003_immutable_ledger"
down_revision: Union[str, None] = "002_data_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Trigger function preventing mutation or deletion of ledger entries (Append-Only)
    op.execute("""
    CREATE OR REPLACE FUNCTION fn_prevent_ledger_entry_mutation()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'ImmutableLedgerViolation: modification or deletion of ledger entries is strictly prohibited.';
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_ledger_entry_mutation ON ledger_entries;
    CREATE TRIGGER trg_prevent_ledger_entry_mutation
    BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW
    EXECUTE FUNCTION fn_prevent_ledger_entry_mutation();
    """)

    # 2. Trigger function enforcing that virtual_accounts.balance strictly matches ledger entries
    op.execute("""
    CREATE OR REPLACE FUNCTION fn_verify_virtual_account_balance()
    RETURNS TRIGGER AS $$
    DECLARE
        v_ledger_sum NUMERIC(12, 2);
        v_expected_balance NUMERIC(12, 2);
    BEGIN
        -- Calculate sum of non-initial ledger entries
        SELECT COALESCE(SUM(amount), 0.0)
        INTO v_ledger_sum
        FROM ledger_entries
        WHERE account_id = NEW.id AND entry_type != 'INITIAL_DEPOSIT';

        v_expected_balance := NEW.initial_balance + v_ledger_sum;

        -- Check if balance matches expected within 1 cent tolerance
        IF ABS(NEW.balance - v_expected_balance) > 0.01 THEN
            RAISE EXCEPTION 'DirectBalanceUpdateViolation: virtual_accounts.balance (%) must equal initial_balance (%) + sum(ledger_entries.amount) (%).',
                NEW.balance, NEW.initial_balance, v_expected_balance;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    DROP TRIGGER IF EXISTS trg_verify_virtual_account_balance ON virtual_accounts;
    CREATE CONSTRAINT TRIGGER trg_verify_virtual_account_balance
    AFTER UPDATE OF balance ON virtual_accounts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION fn_verify_virtual_account_balance();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_verify_virtual_account_balance ON virtual_accounts;")
    op.execute("DROP FUNCTION IF EXISTS fn_verify_virtual_account_balance();")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_ledger_entry_mutation ON ledger_entries;")
    op.execute("DROP FUNCTION IF EXISTS fn_prevent_ledger_entry_mutation();")
