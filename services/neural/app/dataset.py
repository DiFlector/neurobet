"""Multi-sport dataset builder: point-in-time rows from finished matches.

The saved model is trained on every priced snapshot, because live inference
scores the whole timeline. The 2% confirmation rule is applied later, by that
same model, and only on matches it has not seen.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Event, EventStateSnapshot, LLMDecision, OddsSnapshot

from features import FeatureBuilder, TennisFeatureBuilder, registry as feature_registry
from features.tennis import match_is_decided, set_is_open

from .commit import confirming_index, side_odds_from_row
from .context_gate import NEUTRAL_CONTEXT

logger = logging.getLogger("neural.dataset")

_SIDE_A = {"player_a", "team_a", "home", "1", "p1"}
_SIDE_B = {"player_b", "team_b", "away", "2", "p2"}
_DRAWS = {"draw", "x", "tie", "push", "ничья"}
_QUOTE_A = _SIDE_A
_QUOTE_B = _SIDE_B


@dataclass
class MatchSnapshots:
    """Priced point-in-time rows of one finished match. The label is the final winner."""

    event_id: str
    started_at: datetime
    label: int
    rows: List[List[float]]
    observed_at: List[datetime]
    row_labels: Optional[List[int]] = None
    row_markets: Optional[List[str]] = None


def match_winner_label(
    winner: Optional[str],
    participant_a_name: Optional[str],
    participant_b_name: Optional[str],
    score: Optional[Dict[str, Any]],
    sport_code: str,
) -> Optional[int]:
    """1 if side A won, 0 if side B won. Draws and unknown results stay unlabeled."""
    token = str(winner).strip() if winner else ""
    folded = token.lower()
    if folded in _DRAWS:
        return None
    if token:
        if folded in _SIDE_A or (participant_a_name and token == participant_a_name):
            return 1
        if folded in _SIDE_B or (participant_b_name and token == participant_b_name):
            return 0
    return _label_from_score(score or {}, sport_code)


def chronological_folds(
    n_matches: int,
    n_splits: int = 4,
) -> Tuple[str, List[Tuple[List[int], List[int]]]]:
    """Expanding walk-forward by match. A short history falls back to one 70/30 holdout."""
    if n_matches < 4:
        raise ValueError(
            f"Нужно минимум 4 завершённых матча с котировками, сейчас {n_matches}."
        )
    if n_matches < (n_splits + 1) * 2:
        split = max(1, int(n_matches * 0.7))
        if split >= n_matches:
            split = n_matches - 1
        return "holdout", [(list(range(split)), list(range(split, n_matches)))]

    chunk = n_matches // (n_splits + 1)
    folds: List[Tuple[List[int], List[int]]] = []
    for fold in range(n_splits):
        test_start = (fold + 1) * chunk
        test_end = (fold + 2) * chunk if fold < n_splits - 1 else n_matches
        folds.append((list(range(test_start)), list(range(test_start, test_end))))
    return "walk_forward", folds


MAX_TRAIN_ROWS = 12


def _thin_indices(count: int, limit: int) -> List[int]:
    if count <= limit:
        return list(range(count))
    chosen: List[int] = []
    for slot in range(limit):
        index = int(round(slot * (count - 1) / (limit - 1)))
        if not chosen or chosen[-1] != index:
            chosen.append(index)
    return chosen


def thin_match(match: MatchSnapshots, limit: int = MAX_TRAIN_ROWS) -> MatchSnapshots:
    """Evenly spaced snapshots, including the first and the last. Each market is thinned on its own."""
    if not match.row_markets:
        chosen = _thin_indices(len(match.rows), limit)
        return MatchSnapshots(
            event_id=match.event_id,
            started_at=match.started_at,
            label=match.label,
            rows=[match.rows[index] for index in chosen],
            observed_at=[match.observed_at[index] for index in chosen],
            row_labels=None if match.row_labels is None else [match.row_labels[index] for index in chosen],
            row_markets=None,
        )
    groups: Dict[str, List[int]] = {}
    for index, market in enumerate(match.row_markets):
        groups.setdefault(market, []).append(index)
    chosen: List[int] = []
    for market in groups:
        positions = groups[market]
        for slot in _thin_indices(len(positions), limit):
            chosen.append(positions[slot])
    chosen.sort()
    return MatchSnapshots(
        event_id=match.event_id,
        started_at=match.started_at,
        label=match.label,
        rows=[match.rows[index] for index in chosen],
        observed_at=[match.observed_at[index] for index in chosen],
        row_labels=None if match.row_labels is None else [match.row_labels[index] for index in chosen],
        row_markets=[match.row_markets[index] for index in chosen],
    )


def stack_matches(matches: Sequence[MatchSnapshots]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows, labels, and equal weights. Long matches are thinned before this call."""
    rows: List[List[float]] = []
    labels: List[int] = []
    weights: List[float] = []
    for match in matches:
        count = len(match.rows)
        if count == 0:
            continue
        weight = 1.0 / count
        for index, row in enumerate(match.rows):
            rows.append(row)
            labels.append(match.row_labels[index] if match.row_labels else match.label)
            weights.append(weight)
    if not rows:
        return np.empty((0, 0)), np.empty((0,), dtype=int), np.empty((0,))
    return (
        np.asarray(rows, dtype=float),
        np.asarray(labels, dtype=int),
        np.asarray(weights, dtype=float),
    )


def positive_probabilities(model: Any, rows: np.ndarray) -> np.ndarray:
    """P(side A wins) from a fitted classifier, independent of column order."""
    probs = model.predict_proba(rows)
    classes = [int(item) for item in getattr(model, "classes_", [0, 1])]
    if 1 not in classes:
        return np.zeros(len(rows), dtype=float)
    return np.asarray(probs[:, classes.index(1)], dtype=float)


def project_market(match: MatchSnapshots, family: str) -> MatchSnapshots:
    """Keep one market family. ``all`` returns the match unchanged."""
    if family == "all" or not match.row_markets:
        return match
    indexes = []
    for index, market in enumerate(match.row_markets):
        if family == "match" and market == "match_winner":
            indexes.append(index)
        elif family == "set" and str(market).startswith("set_winner:"):
            indexes.append(index)
    return MatchSnapshots(
        event_id=match.event_id,
        started_at=match.started_at,
        label=match.label,
        rows=[match.rows[index] for index in indexes],
        observed_at=[match.observed_at[index] for index in indexes],
        row_labels=None if match.row_labels is None else [match.row_labels[index] for index in indexes],
        row_markets=[match.row_markets[index] for index in indexes],
    )


def _named_value(row: Sequence[float], feature_names: Sequence[str], key: str) -> Optional[float]:
    names = list(feature_names)
    if key not in names:
        return None
    try:
        return float(row[names.index(key)])
    except (TypeError, ValueError, IndexError):
        return None


def decision_records(
    model: Any,
    matches: Sequence[MatchSnapshots],
    feature_names: Sequence[str],
    sport_code: str = "",
) -> List[Dict[str, Any]]:
    """One confirming decision per market, with the odds and match phase used later for slices."""
    records: List[Dict[str, Any]] = []
    for match in matches:
        groups = {"match_winner": list(range(len(match.rows)))}
        if match.row_markets:
            groups = {}
            for index, market in enumerate(match.row_markets):
                groups.setdefault(str(market), []).append(index)
        for market, indexes in groups.items():
            if len(indexes) < 2:
                continue
            matrix = np.asarray([match.rows[index] for index in indexes], dtype=float)
            p_side_a = positive_probabilities(model, matrix)
            pairs = [(float(prob), float(1.0 - prob)) for prob in p_side_a]
            odds = [side_odds_from_row(match.rows[index], feature_names) for index in indexes]
            picked = confirming_index(pairs, odds, market=market, sport_code=sport_code)
            if picked is None:
                continue
            index = indexes[picked]
            label = match.row_labels[index] if match.row_labels else match.label
            chosen_odds = odds[picked][0] if float(p_side_a[picked]) >= 0.5 else odds[picked][1]
            current_set = _named_value(match.rows[index], feature_names, "current_set")
            records.append({
                "event_id": match.event_id,
                "market": market,
                "market_family": "set" if str(market).startswith("set_winner") else "match",
                "label": int(label),
                "probability": float(p_side_a[picked]),
                "odds": float(chosen_odds or 0.0),
                "phase": "unknown" if current_set is None else f"set_{int(current_set)}",
            })
    return records


def decision_scores(
    model: Any,
    matches: Sequence[MatchSnapshots],
    feature_names: Sequence[str],
) -> Tuple[np.ndarray, np.ndarray]:
    """Label and P(side A) at the confirming snapshot. The caller supplies the model."""
    records = decision_records(model, matches, feature_names)
    if not records:
        return np.empty((0,), dtype=int), np.empty((0,), dtype=float)
    return (
        np.asarray([item["label"] for item in records], dtype=int),
        np.asarray([item["probability"] for item in records], dtype=float),
    )


class SportDatasetBuilder:
    """Extracts completed events from the database. Synthetic rows are not produced."""

    def __init__(self, sport_code: str = "tennis", feature_builder: Optional[FeatureBuilder] = None):
        self.sport_code = sport_code.lower()
        if feature_builder is not None:
            self.feature_builder = feature_builder
        else:
            self.feature_builder = feature_registry.get(self.sport_code)
            if self.feature_builder is None:
                raise ValueError(f"No feature builder registered for sport '{self.sport_code}'")

    def load_matches(self, session: Session) -> Tuple[List[MatchSnapshots], List[str]]:
        """Finished numeric-id matches, one priced snapshot per observed state, in start order."""
        events = list(
            session.execute(
                select(Event)
                .where(Event.sport_code == self.sport_code)
                .where(Event.status.in_(["finished", "completed"]))
                .where(Event.source_event_id.op("~")(r"^[0-9]+$"))
                .order_by(Event.scheduled_start_at.asc())
            ).scalars()
        )
        if not events:
            raise ValueError(
                f"В базе данных отсутствуют реальные завершенные события для вида спорта '{self.sport_code}'. "
                f"Генерация синтетических данных отключена: система работает только с реальными данными."
            )

        decisions = list(
            session.execute(
                select(LLMDecision)
                .where(LLMDecision.event_id.in_([event.id for event in events]))
                .order_by(LLMDecision.created_at.asc())
            ).scalars()
        )
        decisions_by_event: Dict[Any, List[LLMDecision]] = {}
        for row in decisions:
            decisions_by_event.setdefault(row.event_id, []).append(row)

        matches: List[MatchSnapshots] = []
        feature_names: List[str] = []
        snapshot_count = 0
        player_history = None
        if self.sport_code == "tennis":
            _backfill_tennis_players(session)
            from .players import PlayerHistory

            player_history = PlayerHistory.load(session)

        for event in events:
            label = match_winner_label(
                _winner_token(event),
                event.participant_a_name,
                event.participant_b_name,
                event.current_score or {},
                self.sport_code,
            )
            if label is None:
                continue

            states = list(
                session.execute(
                    select(EventStateSnapshot)
                    .where(EventStateSnapshot.event_id == event.id)
                    .order_by(EventStateSnapshot.observed_at.asc())
                ).scalars()
            )
            odds = list(
                session.execute(
                    select(OddsSnapshot)
                    .where(OddsSnapshot.event_id == event.id)
                    .order_by(OddsSnapshot.observed_at.asc())
                ).scalars()
            )
            if not odds:
                continue

            cutoffs: List[datetime]
            if states:
                cutoffs = [snapshot.observed_at for snapshot in states]
            else:
                opening = _first_two_sided_time(odds)
                if opening is None:
                    continue
                cutoffs = [opening]

            rows: List[List[float]] = []
            observed: List[datetime] = []
            row_labels: List[int] = []
            row_markets: List[str] = []
            event_decisions = decisions_by_event.get(event.id, [])
            set_winners = (event.metadata_json or {}).get("set_winners") or {}
            for cutoff in cutoffs:
                vector = self.feature_builder.build_features(
                    event_id=str(event.id),
                    cutoff_time=cutoff,
                    event_snapshots=states,
                    odds_snapshots=odds,
                )
                context = _context_as_of(event_decisions, cutoff)
                merged = dict(vector.features)
                merged.update(context)
                if player_history is not None:
                    merged.update(
                        player_history.features(
                            event.participant_a_name,
                            event.participant_b_name,
                            cutoff,
                            exclude_event_id=str(event.id),
                        )
                    )
                state_now = _state_at(states, cutoff)
                if self.sport_code == "tennis":
                    _fill_market_slot(merged, False, 0, True)
                if not feature_names:
                    feature_names = _freeze_names(merged)
                decided = (
                    self.sport_code == "tennis"
                    and state_now is not None
                    and match_is_decided(state_now)
                )
                row = [float(merged.get(name, 0.0)) for name in feature_names]
                odds_a, odds_b = side_odds_from_row(row, feature_names)
                if not decided and odds_a > 1.0 and odds_b > 1.0:
                    rows.append(row)
                    observed.append(cutoff)
                    row_labels.append(label)
                    row_markets.append("match_winner")
                if self.sport_code != "tennis":
                    continue
                current_set = int(float(merged.get("current_set") or 1))
                for raw_number, winner in set_winners.items():
                    try:
                        number = int(raw_number)
                    except (TypeError, ValueError):
                        continue
                    set_label = 1 if winner == "player_a" else 0 if winner == "player_b" else None
                    prices = _prices_as_of(odds, cutoff, "set_winner", number)
                    if set_label is None or prices is None:
                        continue
                    if state_now is not None and not set_is_open(state_now, number):
                        continue
                    slot = dict(merged)
                    if hasattr(self.feature_builder, "apply_market_line"):
                        self.feature_builder.apply_market_line(
                            slot, odds, cutoff, market_type="set_winner", line_value=number
                        )
                    started_set = current_set >= number
                    _fill_market_slot(slot, True, number, started_set, prices[0], prices[1])
                    if not feature_names:
                        feature_names = _freeze_names(slot)
                    rows.append([float(slot.get(name, 0.0)) for name in feature_names])
                    observed.append(cutoff)
                    row_labels.append(set_label)
                    row_markets.append(f"set_winner:{number}")

            if not rows:
                continue
            started = event.scheduled_start_at or observed[0]
            matches.append(
                MatchSnapshots(
                    event_id=str(event.id),
                    started_at=started,
                    label=label,
                    rows=rows,
                    observed_at=observed,
                    row_labels=row_labels,
                    row_markets=row_markets,
                )
            )
            snapshot_count += len(rows)
            if len(matches) == 1 or len(matches) % 10 == 0:
                logger.info(
                    "Dataset %s: %d matches, %d priced snapshots",
                    self.sport_code,
                    len(matches),
                    snapshot_count,
                )

        if len(matches) < 4:
            raise ValueError(
                f"Недостаточно реальных завершенных событий с котировками в базе данных для вида спорта '{self.sport_code}' "
                f"(найдено {len(matches)} матчей, требуется минимум 4). "
                f"Ничьи в выборку не входят. Синтетические данные отключены."
            )
        return matches, feature_names


class TennisDatasetBuilder(SportDatasetBuilder):
    """Backwards-compatible TennisDatasetBuilder."""

    def __init__(self, feature_builder: Optional[TennisFeatureBuilder] = None):
        super().__init__(sport_code="tennis", feature_builder=feature_builder)


def _backfill_tennis_players(session: Session) -> None:
    """Link stored tennis matches to participant rows before a training run."""
    try:
        from tennis_adapter.pipeline import backfill_tennis_participants
    except ImportError:
        logger.info("Tennis participant backfill skipped: tennis adapter is not installed.")
        return
    linked = backfill_tennis_participants(session)
    if linked:
        logger.info("Linked tennis participants on %d matches.", linked)


def get_dataset_builder(sport_code: str = "tennis") -> SportDatasetBuilder:
    """Factory retrieving SportDatasetBuilder for given sport code."""
    return SportDatasetBuilder(sport_code=sport_code)
    """Factory retrieving SportDatasetBuilder for given sport code."""
    return SportDatasetBuilder(sport_code=sport_code)


def _fill_market_slot(
    features: Dict[str, float],
    is_set: bool,
    set_index: int,
    set_has_started: bool,
    odds_a: Optional[float] = None,
    odds_b: Optional[float] = None,
) -> None:
    features["market_is_set"] = 1.0 if is_set else 0.0
    features["set_index"] = float(set_index)
    features["set_has_started"] = 1.0 if set_has_started else 0.0
    features["match_wait"] = 0.0
    if odds_a is None:
        odds_a = features.get("current_odds_player_a", features.get("current_odds_team_a", 0.0))
    if odds_b is None:
        odds_b = features.get("current_odds_player_b", features.get("current_odds_team_b", 0.0))
    features["market_odds_a"] = float(odds_a or 0.0)
    features["market_odds_b"] = float(odds_b or 0.0)


def _prices_as_of(odds: Sequence[Any], cutoff: datetime, market_type: str, line_value: int) -> Optional[Tuple[float, float]]:
    odds_a = None
    odds_b = None
    limit = _as_utc(cutoff)
    for row in odds:
        if _as_utc(row.observed_at) > limit:
            break
        if row.market_type != market_type or row.line_value is None:
            continue
        if int(float(row.line_value)) != line_value:
            continue
        try:
            price = float(row.odds)
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        if row.outcome in ("player_a", "1"):
            odds_a = price
        elif row.outcome in ("player_b", "2"):
            odds_b = price
    if odds_a is None or odds_b is None:
        return None
    return odds_a, odds_b


def _winner_token(event: Event) -> Optional[str]:
    meta = event.metadata_json or {}
    score = event.current_score or {}
    official = meta.get("fonbet_official_result")
    official_winner = official.get("winner") if isinstance(official, dict) else None
    return meta.get("winner") or meta.get("participant_winner") or score.get("winner") or official_winner


def _label_from_score(score: Dict[str, Any], sport_code: str) -> Optional[int]:
    if sport_code in ("tennis", "volleyball"):
        return _compare(score.get("sets_a"), score.get("sets_b"))
    if sport_code == "basketball":
        return _compare(score.get("score_a", score.get("pts_a")), score.get("score_b", score.get("pts_b")))
    return _compare(score.get("score_a"), score.get("score_b"))


def _compare(left: Any, right: Any) -> Optional[int]:
    if left is None or right is None:
        return None
    try:
        side_a = int(left)
        side_b = int(right)
    except (TypeError, ValueError):
        return None
    if side_a == side_b:
        return None
    return 1 if side_a > side_b else 0


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _state_at(states: Sequence[Any], cutoff: datetime) -> Optional[Any]:
    """Latest score snapshot that was already observed at this cutoff."""
    limit = _as_utc(cutoff)
    chosen = None
    for state in states:
        if _as_utc(state.observed_at) > limit:
            break
        chosen = state
    return chosen


def _unit(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _context_as_of(decisions: Sequence[LLMDecision], cutoff: datetime) -> Dict[str, float]:
    """Latest numeric context whose created_at is at or before the snapshot."""
    chosen: Optional[Dict[str, Any]] = None
    limit = _as_utc(cutoff)
    for row in decisions:
        if _as_utc(row.created_at) > limit:
            break
        raw = row.raw_json if isinstance(row.raw_json, dict) else {}
        if "context_side_a" in raw:
            chosen = raw
    if not chosen:
        return dict(NEUTRAL_CONTEXT)
    return {key: _unit(chosen.get(key), default) for key, default in NEUTRAL_CONTEXT.items()}


def _freeze_names(features: Dict[str, float]) -> List[str]:
    names = sorted(key for key in features if key not in NEUTRAL_CONTEXT)
    for key in NEUTRAL_CONTEXT:
        names.append(key)
    return names


def _first_two_sided_time(odds: Sequence[Any]) -> Optional[datetime]:
    seen_a = False
    seen_b = False
    for quote in odds:
        outcome = str(getattr(quote, "outcome", "") or "").lower()
        try:
            price = float(quote.odds)
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        if outcome in _QUOTE_A:
            seen_a = True
        elif outcome in _QUOTE_B:
            seen_b = True
        if seen_a and seen_b:
            return quote.observed_at
    return None
