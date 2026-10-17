"""Multi-sport dataset builder: point-in-time rows from finished matches.

The saved model is trained on every priced snapshot, because live inference
scores the whole timeline. The 2% confirmation rule is applied later, by that
same model, and only on matches it has not seen.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Event, EventStateSnapshot, LLMDecision, OddsSnapshot

from features import FeatureBuilder, TennisFeatureBuilder, registry as feature_registry
from features.tennis import closed_set_winner, is_one_set_lead, match_is_decided, set_is_open

from .commit import confirming_index, side_odds_from_row
from .strategy import stake_gate
from .context_gate import NEUTRAL_CONTEXT, context_side_from_event

logger = logging.getLogger("neural.dataset")

_SIDE_A = {"player_a", "team_a", "home", "1", "p1"}
_SIDE_B = {"player_b", "team_b", "away", "2", "p2"}
_DRAWS = {"draw", "x", "tie", "push", "ничья"}
_QUOTE_A = _SIDE_A
_QUOTE_B = _SIDE_B
_MAX_TRAIN_ODDS = 100.0
_MIN_OVERROUND = 1.02
_MAX_OVERROUND = 1.35
_BLOCKED_RESULTS = {"RETIRED", "VOID", "CANCELLED"}
_LEADING_SETS = re.compile(r"^\s*(\d+)\s*[:\-]\s*(\d+)")
_PAREN_GAMES = re.compile(r"(\d+)\s*[-:]\s*(\d+)")


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


@dataclass
class DatasetFilterCounts:
    """How many matches and rows the training load left out, by reason."""

    duplicate_event: int = 0
    label_conflict: int = 0
    retired_or_walkover: int = 0
    incomplete_tennis: int = 0
    unlabeled: int = 0
    broken_line: int = 0
    repeated_state: int = 0
    set_label_mismatch: int = 0
    by_reason: Dict[str, int] = field(default_factory=dict)

    def add_match(self, reason: str) -> None:
        self.by_reason[reason] = self.by_reason.get(reason, 0) + 1
        current = getattr(self, reason, None)
        if isinstance(current, int):
            setattr(self, reason, current + 1)

    def log(self, sport_code: str) -> None:
        logger.info(
            "Dataset %s excluded: matches duplicate=%d label_conflict=%d retired=%d "
            "incomplete=%d unlabeled=%d; rows broken_line=%d repeated_state=%d set_label_mismatch=%d",
            sport_code,
            self.duplicate_event,
            self.label_conflict,
            self.retired_or_walkover,
            self.incomplete_tennis,
            self.unlabeled,
            self.broken_line,
            self.repeated_state,
            self.set_label_mismatch,
        )


def choose_canonical_events(
    events: Sequence[Any],
    odds_counts: Dict[Any, int],
) -> Tuple[List[Any], int]:
    """One row per source id. The copy with more line snapshots stays."""
    groups: Dict[str, List[Any]] = {}
    order: List[str] = []
    for event in events:
        key = str(getattr(event, "source_event_id", "") or "")
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(event)
    kept: List[Any] = []
    dropped = 0
    for key in order:
        group = groups[key]
        if len(group) == 1:
            kept.append(group[0])
            continue
        kept.append(max(group, key=_richness(odds_counts)))
        dropped += len(group) - 1
    return kept, dropped


def match_training_exclusion(
    sport_code: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    score: Optional[Dict[str, Any]] = None,
    participant_a_name: Optional[str] = None,
    participant_b_name: Optional[str] = None,
) -> Optional[str]:
    """Why a finished match must stay out of training. None means it can be labeled."""
    meta = metadata or {}
    board = score or {}
    official = meta.get("fonbet_official_result")
    official = official if isinstance(official, dict) else {}
    if _result_is_blocked(official) or _result_is_blocked(board):
        return "retired_or_walkover"
    tally = _set_tally(official) or _set_tally(board)
    if sport_code == "tennis" and tally is not None and is_one_set_lead(*tally):
        return "incomplete_tennis"
    sides = [
        side
        for side in (
            _side_from_token(
                meta.get("winner") or meta.get("participant_winner") or board.get("winner"),
                participant_a_name,
                participant_b_name,
            ),
            _side_from_token(official.get("winner"), participant_a_name, participant_b_name),
            _tally_side(board, sport_code),
            _tally_side(official, sport_code),
        )
        if side is not None
    ]
    if len(set(sides)) > 1:
        return "label_conflict"
    return None


def line_is_trainable(odds_a: float, odds_b: float) -> bool:
    """Both prices are real and the margin sits inside the training window."""
    try:
        left = float(odds_a)
        right = float(odds_b)
    except (TypeError, ValueError):
        return False
    if left <= 1.0 or right <= 1.0 or left > _MAX_TRAIN_ODDS or right > _MAX_TRAIN_ODDS:
        return False
    overround = (1.0 / left) + (1.0 / right)
    return _MIN_OVERROUND <= overround <= _MAX_OVERROUND


def collapse_repeated_states(states: Sequence[Any], odds: Sequence[Any]) -> Tuple[List[Any], int]:
    """Drop a snapshot that repeats the previous score and the same two-sided prices."""
    kept: List[Any] = []
    dropped = 0
    previous = None
    for state in states:
        signature = (_score_fingerprint(state), _book_as_of(odds, state.observed_at))
        if previous == signature:
            dropped += 1
            continue
        previous = signature
        kept.append(state)
    return kept, dropped


def set_label_matches(winner: str, games: Optional[Tuple[Any, Any]]) -> bool:
    """True when the stored set winner agrees with that set's closed games.

    Missing games stay in the sample: there is nothing to disagree with.
    """
    if games is None:
        return True
    return closed_set_winner(games[0], games[1]) == winner


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


def example_weights(
    base_weights: np.ndarray,
    probabilities: Optional[np.ndarray],
    labels: np.ndarray,
    gamma: float = 0.0,
) -> np.ndarray:
    """Match-equal weights. A positive gamma raises the weight of a row the first fit got wrong.

    gamma 0 returns the base weights unchanged. The multiplier is (1 - p of the true side) ** gamma.
    """
    weights = np.asarray(base_weights, dtype=float)
    if float(gamma) == 0.0 or probabilities is None:
        return np.array(weights, dtype=float, copy=True)
    probs = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1.0 - 1e-6)
    truth = np.asarray(labels, dtype=int)
    p_true = np.where(truth == 1, probs, 1.0 - probs)
    return weights * np.power(1.0 - p_true, float(gamma))


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
            probability = float(p_side_a[picked])
            chosen_probability = probability if probability >= 0.5 else 1.0 - probability
            correct = (int(label) == 1) == (probability >= 0.5)
            in_window = bool(stake_gate(chosen_probability, float(chosen_odds or 0.0), market, sport_code)[0])
            records.append({
                "event_id": match.event_id,
                "market": market,
                "market_family": "set" if str(market).startswith("set_winner") else "match",
                "label": int(label),
                "probability": probability,
                "odds": float(chosen_odds or 0.0),
                "phase": "unknown" if current_set is None else f"set_{int(current_set)}",
                "sport_code": sport_code,
                "in_bet_window": in_window,
                "critical_miss": (not correct) and in_window,
                "confident_miss": (not correct) and chosen_probability >= 0.75,
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
        """Read the saved training tape. Building rows is the refresh job, not training."""
        version = str(getattr(self.feature_builder, "feature_version", "0"))
        from .dataset_cache import read_store

        stored = read_store(self.sport_code, version)
        if stored is None:
            raise RuntimeError(
                f"Сохранённая лента '{self.sport_code}' ещё не собрана. Её пишет dataset_refresh."
            )
        return stored

    def list_events(self, session: Session) -> List[Event]:
        """Finished numeric-id matches in start order, one row per real event."""
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

        odds_counts = {
            event_id: int(count)
            for event_id, count in session.execute(
                select(OddsSnapshot.event_id, func.count())
                .where(OddsSnapshot.event_id.in_([event.id for event in events]))
                .group_by(OddsSnapshot.event_id)
            ).all()
        }
        events, _duplicate_matches = choose_canonical_events(events, odds_counts)
        return events

    def build_matches(
        self,
        session: Session,
        events: Sequence[Event],
        feature_names: Optional[List[str]] = None,
    ) -> Tuple[List[MatchSnapshots], List[str], List[str]]:
        """Turn the given matches into training rows. Already saved matches are not passed in."""
        events = list(events)
        counts = DatasetFilterCounts()
        feature_names = list(feature_names or [])
        if not events:
            return [], feature_names, []

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
        snapshot_count = 0
        player_history = None
        if self.sport_code == "tennis":
            _backfill_tennis_players(session)
            from .players import PlayerHistory

            player_history = PlayerHistory.load(session)

        for event in events:
            exclusion = match_training_exclusion(
                self.sport_code,
                metadata=event.metadata_json,
                score=event.current_score,
                participant_a_name=event.participant_a_name,
                participant_b_name=event.participant_b_name,
            )
            if exclusion is not None:
                counts.add_match(exclusion)
                continue
            label = match_winner_label(
                _winner_token(event),
                event.participant_a_name,
                event.participant_b_name,
                event.current_score or {},
                self.sport_code,
            )
            if label is None:
                counts.add_match("unlabeled")
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

            states, repeated = collapse_repeated_states(states, odds)
            counts.repeated_state += repeated
            set_games = _closed_set_games(event, states) if self.sport_code == "tennis" else {}

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
                saved_side = context_side_from_event(event)
                if saved_side is not None:
                    context["context_side_a"] = saved_side
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
                    if line_is_trainable(odds_a, odds_b):
                        rows.append(row)
                        observed.append(cutoff)
                        row_labels.append(label)
                        row_markets.append("match_winner")
                    else:
                        counts.broken_line += 1
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
                    if not set_label_matches(str(winner), set_games.get(number)):
                        counts.set_label_mismatch += 1
                        continue
                    if not line_is_trainable(prices[0], prices[1]):
                        counts.broken_line += 1
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

        counts.log(self.sport_code)
        built = {match.event_id for match in matches}
        skipped = [str(event.id) for event in events if str(event.id) not in built]
        return matches, feature_names, skipped


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


def _richness(odds_counts: Dict[Any, int]):
    def key(event: Any) -> Tuple[int, float, str]:
        seen = getattr(event, "last_seen_at", None)
        stamp = seen.timestamp() if isinstance(seen, datetime) else 0.0
        return (int(odds_counts.get(getattr(event, "id", None), 0)), stamp, str(getattr(event, "id", "")))

    return key


def _side_from_token(
    winner: Optional[str],
    participant_a_name: Optional[str],
    participant_b_name: Optional[str],
) -> Optional[int]:
    """Map a winner token to a side. An unknown token is not a side."""
    token = str(winner).strip() if winner else ""
    if not token:
        return None
    folded = token.lower()
    if folded in _DRAWS:
        return None
    if folded in _SIDE_A or (participant_a_name and token == participant_a_name):
        return 1
    if folded in _SIDE_B or (participant_b_name and token == participant_b_name):
        return 0
    return None


def _result_is_blocked(payload: Any) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    if payload.get("is_retired") or payload.get("is_walkover"):
        return True
    return str(payload.get("status") or "").upper() in _BLOCKED_RESULTS


def _leading_sets(text: Any) -> Optional[Tuple[int, int]]:
    match = _LEADING_SETS.match(str(text or "").strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _set_tally(payload: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(payload, dict):
        return None
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    left = payload.get("sets_a", stats.get("sets_a"))
    right = payload.get("sets_b", stats.get("sets_b"))
    if left is not None and right is not None:
        try:
            return int(left), int(right)
        except (TypeError, ValueError):
            pass
    return _leading_sets(payload.get("final_score") or payload.get("score"))


def _tally_side(payload: Any, sport_code: str) -> Optional[int]:
    if not isinstance(payload, dict) or not payload:
        return None
    if sport_code in ("tennis", "volleyball"):
        pair = _set_tally(payload)
        if pair is not None:
            return None if pair[0] == pair[1] else (1 if pair[0] > pair[1] else 0)
    return _label_from_score(payload, sport_code)


def _score_fingerprint(state: Any) -> str:
    detail = getattr(state, "score_detail", None) or {}
    if not isinstance(detail, dict):
        detail = {}
    sport = getattr(state, "sport_state", None) or {}
    stats = sport.get("stats") if isinstance(sport, dict) else {}
    if not isinstance(stats, dict):
        stats = {}
    payload = {
        "score": getattr(state, "score", None),
        "period": getattr(state, "current_period", None),
        "sets": detail.get("sets"),
        "game": detail.get("current_game"),
        "quarters": detail.get("quarters"),
        "set_lines": stats.get("set_lines") or detail.get("set_lines"),
        "sets_a": stats.get("sets_a", detail.get("sets_a")),
        "sets_b": stats.get("sets_b", detail.get("sets_b")),
        "score_a": detail.get("score_a", stats.get("score_a")),
        "score_b": detail.get("score_b", stats.get("score_b")),
    }
    return json.dumps(payload, sort_keys=True, default=str)


def _quote_key(row: Any) -> Optional[Tuple[Tuple[str, int, str], float]]:
    market = str(getattr(row, "market_type", "") or "match_winner")
    if market not in ("match_winner", "set_winner"):
        return None
    line = getattr(row, "line_value", None)
    if market == "set_winner":
        if line is None:
            return None
        try:
            line_key = int(float(line))
        except (TypeError, ValueError):
            return None
    else:
        line_key = 0
    outcome = str(getattr(row, "outcome", "") or "").lower()
    if outcome in _QUOTE_A:
        side = "a"
    elif outcome in _QUOTE_B:
        side = "b"
    else:
        return None
    try:
        price = round(float(row.odds), 4)
    except (TypeError, ValueError):
        return None
    if price <= 1.0:
        return None
    return (market, line_key, side), price


def _book_as_of(odds: Sequence[Any], cutoff: datetime) -> Tuple:
    """Latest two-sided prices at or before cutoff. Odds must be time-ordered."""
    limit = _as_utc(cutoff)
    latest: Dict[Tuple[str, int, str], float] = {}
    for row in odds:
        observed = getattr(row, "observed_at", None)
        if observed is None:
            continue
        if _as_utc(observed) > limit:
            break
        parsed = _quote_key(row)
        if parsed is None:
            continue
        key, price = parsed
        latest[key] = price
    books = []
    markets = sorted({(market, line) for market, line, _side in latest})
    for market, line in markets:
        left = latest.get((market, line, "a"))
        right = latest.get((market, line, "b"))
        if left is None or right is None:
            continue
        books.append((market, line, left, right))
    return tuple(books)


def _paren_games(text: Any) -> Dict[int, Tuple[int, int]]:
    raw = str(text or "")
    start, end = raw.find("("), raw.rfind(")")
    if start < 0 or end <= start:
        return {}
    found: Dict[int, Tuple[int, int]] = {}
    for index, pair in enumerate(_PAREN_GAMES.finditer(raw[start + 1:end]), start=1):
        found[index] = (int(pair.group(1)), int(pair.group(2)))
    return found


def _index_set_lines(lines: Any) -> Dict[int, Tuple[int, int]]:
    found: Dict[int, Tuple[int, int]] = {}
    for index, line in enumerate(lines or [], start=1):
        if not isinstance(line, dict):
            continue
        try:
            number = int(line.get("set_number") or index)
            found[number] = (int(line["games_a"]), int(line["games_b"]))
        except (TypeError, ValueError, KeyError):
            continue
    return found


def _structured_set_lines(event: Any, states: Sequence[Any]) -> Any:
    for state in reversed(list(states)):
        sport = getattr(state, "sport_state", None) or {}
        stats = sport.get("stats") if isinstance(sport, dict) else {}
        if isinstance(stats, dict) and stats.get("set_lines"):
            return stats["set_lines"]
        detail = getattr(state, "score_detail", None) or {}
        if isinstance(detail, dict) and (detail.get("set_lines") or detail.get("sets")):
            return detail.get("set_lines") or detail.get("sets")
    score = getattr(event, "current_score", None) or {}
    if isinstance(score, dict):
        stats = score.get("stats") if isinstance(score.get("stats"), dict) else {}
        return stats.get("set_lines") or score.get("set_lines") or score.get("sets") or []
    return []


def _closed_set_games(event: Any, states: Sequence[Any]) -> Dict[int, Tuple[int, int]]:
    """Closed games of each set from the official score, then from the last board."""
    texts: List[Any] = []
    score = getattr(event, "current_score", None) or {}
    meta = getattr(event, "metadata_json", None) or {}
    official = meta.get("fonbet_official_result") if isinstance(meta, dict) else None
    if isinstance(official, dict):
        texts.extend([official.get("final_score"), official.get("score")])
    if isinstance(score, dict):
        texts.extend([score.get("final_score"), score.get("score")])
    for state in reversed(list(states)):
        texts.append(getattr(state, "score", None))
    for text in texts:
        parsed = _paren_games(text)
        if parsed:
            return parsed
    return _index_set_lines(_structured_set_lines(event, states))
