"""Dashboard market-group chips for LIVE predictions."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from neurobet_filters import (  # noqa: E402
    DRAW_FACTOR_ID,
    W1_FACTOR_ID,
    dashboard_market_group,
    market_group_sql,
    normalize_market_group,
)


def test_normalize_market_group():
    assert normalize_market_group(None) == "all"
    assert normalize_market_group("1x2") == "result"
    assert normalize_market_group("fora") == "handicap"
    assert normalize_market_group("nope") == "all"


def test_dashboard_market_group_by_factor():
    assert dashboard_market_group(W1_FACTOR_ID) == "result"
    assert dashboard_market_group(DRAW_FACTOR_ID) == "result"
    assert dashboard_market_group(930) == "totals"
    assert dashboard_market_group(927) == "handicap"
    assert dashboard_market_group(1809) == "itotal"
    assert dashboard_market_group(924) == "other"


def test_dashboard_market_group_by_label():
    assert dashboard_market_group(99999, label="Фора 1 (-1.5)") == "handicap"
    assert dashboard_market_group(99999, label="Индивидуальный тотал 1 Больше") == "itotal"
    assert dashboard_market_group(99999, label="Тотал Больше") == "totals"


def test_market_group_sql_all_is_empty():
    sql, params = market_group_sql("l", "all")
    assert sql == ""
    assert params == []


def test_market_group_sql_result_binds_1x2():
    sql, params = market_group_sql("l", "result")
    assert "l.factor_id = ANY(%s)" in sql
    assert W1_FACTOR_ID in params[0]
    assert DRAW_FACTOR_ID in params[0]
    assert 923 in params[0]
