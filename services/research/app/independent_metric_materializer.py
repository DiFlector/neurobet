"""Causal canonical tennis metric materializer used by the source worker."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tennis_adapter.independent_metrics import digest_metrics, replay_independent_metrics

logger = logging.getLogger("research.independent_metrics")


def _recover_official_ranking_profile(state: dict[str, Any]) -> dict[str, Any] | None:
    """Reparse archived profile bytes when an older parser omitted rank evidence."""
    profile = state.get("profile") or {}
    if profile.get("ranking_status"):
        return profile
    key, expected_hash = state.get("profile_raw_object_key"), state.get("profile_sha256")
    if not isinstance(key, str) or not isinstance(expected_hash, str):
        return profile
    try:
        from .storage import ResearchStorage
        from tennis_adapter.external_names import fold

        body = ResearchStorage().read_external_raw_bytes(key)
        if body is None or hashlib.sha256(body).hexdigest() != expected_hash:
            return profile
        document = json.loads(body)
        raw = document.get("data", document) if isinstance(document, dict) else None
        if not isinstance(raw, dict) or str(raw.get("id")) != str(profile.get("player_id")):
            return profile
        name = str(raw.get("name") or raw.get("full_name") or "")
        if fold(name) != fold(str(profile.get("name") or "")) or raw.get("is_doubles_team") is True:
            return profile
        rank = raw.get("ranking")
        points = raw.get("ranking_points")
        return {**profile,
                "ranking": rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
                "ranking_points": points if isinstance(points, int) and not isinstance(points, bool) else None,
                "ranking_status": str(raw.get("ranking_status") or "").lower() or None,
                "ranking_checked_at": raw.get("ranking_checked_at")}
    except Exception:
        logger.exception("Could not reparse archived live ranking profile")
        return profile


def _engine():
    from db.connection import get_database_url

    return create_engine(get_database_url(), poolclass=NullPool, pool_pre_ping=True)


def materialize_independent_tennis_metrics() -> dict[str, Any]:
    """Replay changed canonical history and append snapshots for confirmed profiles.

    The canonical match table is already one-row-per-match. The database cursor is
    streamed in chronological order so this full dependency replay stays bounded.
    """
    engine = _engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO ext_tennis_canonical_player_aliases
                  (id, source, source_player_id, canonical_player_id, status, evidence_json, created_at)
                SELECT gen_random_uuid(), 'flashscore', l.source_player_id, p.canonical_player_id,
                       'confirmed', jsonb_build_object(
                         'method','confirmed_participant_link_and_archive_alias',
                         'identity_link_id',l.id::text,
                         'archive_source',a.source,
                         'archive_source_player_id',a.source_player_id,
                         'confirmed_at',l.available_at
                       ), now()
                FROM (
                  SELECT DISTINCT ON (participant_id) id, participant_id, source_player_id, status, available_at
                  FROM tennis_player_identity_links
                  WHERE source='flashscore'
                  ORDER BY participant_id, available_at DESC, id DESC
                ) l
                JOIN tennis_player_profiles p ON p.participant_id=l.participant_id
                JOIN ext_tennis_canonical_player_aliases a
                  ON a.canonical_player_id=p.canonical_player_id AND a.status='confirmed'
                 AND p.source_identities ? ('archive:' || a.source)
                 AND p.source_identities->>('archive:' || a.source)=a.source_player_id
                WHERE l.status='confirmed' AND p.canonical_player_id IS NOT NULL
                  AND p.source_identities->>'flashscore'=l.source_player_id
                ON CONFLICT DO NOTHING
            """))
            conn.commit()

            # The live ranking adapter searches by exact full name, rejects
            # ambiguity, then verifies the fetched profile repeats that name
            # and provider ID. Bind it to a canonical player only when the
            # participant already has confirmed Flashscore and archive IDs,
            # the exact canonical display name agrees, and tour agrees with
            # the canonical gender.
            profile_states = conn.execute(text("""
                SELECT s.state_key, s.state_json, p.canonical_player_id::text AS canonical_id,
                       c.display_name, c.gender
                FROM external_tennis_source_state s
                JOIN tennis_player_profiles p
                  ON s.state_key='profile:' || p.participant_id::text
                JOIN ext_tennis_canonical_players c ON c.id=p.canonical_player_id
                JOIN LATERAL (
                  SELECT status FROM tennis_player_identity_links
                  WHERE participant_id=p.participant_id AND source='flashscore'
                  ORDER BY available_at DESC,id DESC LIMIT 1
                ) l ON l.status='confirmed'
                WHERE s.source='live_tennis_api' AND s.state_key LIKE 'profile:%'
                  AND s.state_json->>'status'='200'
                  AND p.canonical_player_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM ext_tennis_canonical_player_aliases a
                    WHERE a.canonical_player_id=p.canonical_player_id AND a.status='confirmed'
                      AND p.source_identities ? ('archive:' || a.source)
                      AND p.source_identities->>('archive:' || a.source)=a.source_player_id
                  )
                ORDER BY s.updated_at DESC
            """)).mappings().all()
            from tennis_adapter.external_names import fold
            for row in profile_states:
                state = row["state_json"] or {}
                profile = _recover_official_ranking_profile(state) or {}
                rank, source_id = profile.get("ranking"), profile.get("player_id")
                observed = state.get("profile_fetched_at") or state.get("observed_at")
                tour = str(profile.get("tour") or "").lower()
                expected_tour = "wta" if str(row["gender"]).lower() in {"f", "w", "female"} else (
                    "atp" if str(row["gender"]).lower() in {"m", "male"} else None
                )
                if (not source_id or not observed
                        or not expected_tour or tour != expected_tour
                        or fold(str(profile.get("name") or "")) != fold(str(row["display_name"]))
                        or not state.get("profile_raw_object_key") or not state.get("profile_sha256")):
                    continue
                conn.execute(text("""
                    INSERT INTO ext_tennis_canonical_player_aliases
                      (id,source,source_player_id,canonical_player_id,status,evidence_json,created_at)
                    VALUES (gen_random_uuid(),'live_tennis_api',:source_id,:canonical_id,'confirmed',
                      CAST(:evidence AS jsonb),:observed)
                    ON CONFLICT DO NOTHING
                """), {"source_id": str(source_id), "canonical_id": row["canonical_id"],
                       "evidence": json.dumps({"method": "exact_unique_provider_profile",
                           "participant_state_key": row["state_key"],
                           "raw_object_key": state["profile_raw_object_key"],
                           "content_sha256": state["profile_sha256"], "tour": tour,
                           "ranking_status": profile.get("ranking_status"),
                           "ranking_checked_at": profile.get("ranking_checked_at")}),
                       "observed": datetime.fromisoformat(str(observed).replace("Z", "+00:00"))})
            conn.commit()
            generation, processed = conn.execute(text("""
                SELECT generation, processed_generation
                FROM tennis_independent_metric_refresh_state WHERE id=1
            """)).one()
            targets = conn.execute(text("""
                WITH latest_flashscore_link AS (
                  SELECT DISTINCT ON (participant_id) participant_id, status
                  FROM tennis_player_identity_links
                  WHERE source='flashscore'
                  ORDER BY participant_id, available_at DESC, id DESC
                )
                SELECT DISTINCT p.canonical_player_id::text
                FROM tennis_player_profiles p
                JOIN latest_flashscore_link l ON l.participant_id=p.participant_id
                JOIN ext_tennis_canonical_player_aliases a
                  ON a.canonical_player_id=p.canonical_player_id AND a.status='confirmed'
                 AND p.source_identities ? ('archive:' || a.source)
                 AND p.source_identities->>('archive:' || a.source)=a.source_player_id
                WHERE l.status='confirmed' AND p.canonical_player_id IS NOT NULL
            """)).scalars().all()
            targets = set(targets)
            if not targets:
                return {"status": "no_confirmed_targets", "generation": generation,
                        "snapshots_written": 0}
            if generation == processed:
                stale_target = False
                for target_id in targets:
                    latest_formula = conn.execute(text("""
                        SELECT metrics_json->>'formula_version'
                        FROM tennis_canonical_metric_snapshots
                        WHERE canonical_player_id=:id
                        ORDER BY metrics_version DESC LIMIT 1
                    """), {"id": target_id}).scalar()
                    if latest_formula != "tennis_independent_metrics_v4":
                        stale_target = True
                        break
                if not stale_target:
                    return {"status": "unchanged", "generation": generation, "snapshots_written": 0}

        cutoff = datetime.now(timezone.utc)
        input_hasher = hashlib.sha256()
        serve_counts_by_match: dict[str, dict[str, tuple[float, float]]] = {}
        with engine.connect().execution_options(stream_results=True) as conn:
            fact_rows = conn.execute(text("""
                SELECT DISTINCT ON (
                         source_payload->>'canonical_match_id',
                         source_payload->>'canonical_player_id'
                       ) source_payload->>'canonical_match_id' AS match_id,
                         source_payload->>'canonical_player_id' AS player_id,
                         serve_points, serve_points_won, available_at
                FROM tennis_player_result_facts
                WHERE source='canonical_archive' AND valid IS TRUE
                  AND available_at <= :cutoff
                  AND source_payload ? 'canonical_match_id'
                  AND source_payload ? 'canonical_player_id'
                  AND serve_points > 0 AND serve_points_won BETWEEN 0 AND serve_points
                ORDER BY source_payload->>'canonical_match_id',
                         source_payload->>'canonical_player_id', available_at DESC, id DESC
            """), {"cutoff": cutoff}).mappings()
            for fact in fact_rows:
                match_id, player_id = fact["match_id"], fact["player_id"]
                serve_counts_by_match.setdefault(match_id, {})[player_id] = (
                    float(fact["serve_points"]), float(fact["serve_points_won"])
                )
                input_hasher.update(json.dumps(
                    [match_id, player_id, fact["serve_points"], fact["serve_points_won"], fact["available_at"]],
                    separators=(",", ":"), default=str,
                ).encode())

        def rows():
            with engine.connect().execution_options(stream_results=True) as conn:
                result = conn.execute(text("""
                  SELECT id::text AS canonical_match_id, played_at, available_at,
                         winner_player_id::text AS winner_id, loser_player_id::text AS loser_id,
                         surface, serve_won_winner, serve_won_loser, stats_source, stats
                  FROM ext_tennis_canonical_matches
                  WHERE duplicate_of_id IS NULL
                    AND winner_player_id IS NOT NULL AND loser_player_id IS NOT NULL
                    AND played_at <= :cutoff AND available_at <= :cutoff
                    AND score !~* '(retired|walkover|w/o|void|abandon|cancel|default)'
                  ORDER BY played_at, id
                """), {"cutoff": cutoff}).mappings()
                for row in result:
                    item = dict(row)
                    counts = serve_counts_by_match.get(item["canonical_match_id"], {})
                    item["serve_points_a"], item["serve_points_won_a"] = counts.get(item["winner_id"], (None, None))
                    item["serve_points_b"], item["serve_points_won_b"] = counts.get(item["loser_id"], (None, None))
                    input_hasher.update(json.dumps(
                        [item["canonical_match_id"], item["played_at"], item["available_at"],
                         item["winner_id"], item["loser_id"], item["surface"],
                         item["serve_points_a"], item["serve_points_won_a"],
                         item["serve_points_b"], item["serve_points_won_b"],
                         item["stats_source"], item["stats"]],
                        sort_keys=True, separators=(",", ":"), default=str,
                    ).encode())
                    yield item

        metrics = replay_independent_metrics(
            rows(), cutoff, canonical_sorted=True, head_to_head_player_ids=targets
        )
        dependency_digest = input_hasher.hexdigest()
        written = 0
        with engine.begin() as conn:
            current_generation = conn.execute(text("""
                SELECT generation FROM tennis_independent_metric_refresh_state WHERE id=1
            """)).scalar_one()
            if current_generation < generation:
                raise RuntimeError("independent metric generation moved backwards")

            for player_id in sorted(targets):
                payload = metrics.get(player_id)
                if payload is None:
                    continue
                player = conn.execute(text("""
                    SELECT gender, display_name FROM ext_tennis_canonical_players WHERE id=:id
                """), {"id": player_id}).mappings().first()
                if player is None:
                    continue
                snapshot_payload = {**payload, "gender": player["gender"],
                                    "display_name": player["display_name"],
                                    "dependency_digest": dependency_digest,
                                    "formula_version": "tennis_independent_metrics_v4"}
                player_digest = digest_metrics(snapshot_payload)
                prior = conn.execute(text("""
                    SELECT history_digest FROM tennis_canonical_metric_snapshots
                    WHERE canonical_player_id=:id ORDER BY metrics_version DESC LIMIT 1
                """), {"id": player_id}).scalar()
                if prior == player_digest:
                    continue
                version = conn.execute(text("""
                    SELECT COALESCE(MAX(metrics_version),0)+1
                    FROM tennis_canonical_metric_snapshots WHERE canonical_player_id=:id
                """), {"id": player_id}).scalar_one()
                conn.execute(text("""
                    INSERT INTO tennis_canonical_metric_snapshots
                      (id, canonical_player_id, metrics_version, available_at, source_watermark,
                       history_digest, metrics_json, created_at)
                    VALUES (gen_random_uuid(), :player, :version, :available, :watermark,
                            :digest, CAST(:payload AS jsonb), now())
                """), {"player": player_id, "version": version, "available": cutoff,
                       "watermark": payload.get("source_watermark"), "digest": player_digest,
                       "payload": json.dumps(snapshot_payload, default=str)})
                written += 1

            for row in profile_states:
                state = row["state_json"] or {}
                profile = _recover_official_ranking_profile(state) or {}
                source_id, observed = profile.get("player_id"), state.get("observed_at")
                rank, tour = profile.get("ranking"), str(profile.get("tour") or "").lower()
                ranking_status = profile.get("ranking_status")
                checked_at = profile.get("ranking_checked_at")
                expected_tour = "wta" if str(row["gender"]).lower() in {"f", "w", "female"} else (
                    "atp" if str(row["gender"]).lower() in {"m", "male"} else None
                )
                available_at = state.get("profile_fetched_at") or observed
                if (not source_id or ranking_status not in {"ranked", "unranked"}
                        or (ranking_status == "ranked" and (not isinstance(rank, int) or rank <= 0))
                        or not observed
                        or tour != expected_tour or not state.get("profile_raw_object_key")
                        or not state.get("profile_sha256") or not available_at):
                    continue
                # The FREE profile endpoint confirms the current position against
                # the published ATP/WTA table but does not expose its publication
                # week. Keep ranking_date NULL; available_at is the real fetched
                # observation time and is the only temporal key used by inference.
                checked = None
                if checked_at:
                    try:
                        checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                fetched = datetime.fromisoformat(str(available_at).replace("Z", "+00:00"))
                if checked is None or checked > fetched:
                    continue
                conn.execute(text("""
                    INSERT INTO tennis_player_ranking_snapshots
                       (id,source,source_player_id,canonical_player_id,tour,ranking_date,rank,points,
                       available_at,source_ref,source_content_hash,source_url,parser_version,ranking_status,is_singles,created_at)
                    SELECT gen_random_uuid(),'live_tennis_api_official',:source_id,:canonical_id,:tour,NULL,
                           :rank,:points,:available_at,:source_ref,:content_hash,:source_url,:parser_version,:ranking_status,true,now()
                    WHERE EXISTS (
                      SELECT 1 FROM ext_tennis_canonical_player_aliases a
                      WHERE a.source='live_tennis_api' AND a.source_player_id=:source_id
                        AND a.canonical_player_id=:canonical_id AND a.status='confirmed'
                    )
                    ON CONFLICT DO NOTHING
                """), {"source_id": str(source_id), "canonical_id": row["canonical_id"],
                       "tour": tour, "available_at": fetched,
                       "rank": rank if ranking_status == "ranked" else None,
                       "points": profile.get("ranking_points") if ranking_status == "ranked" else None,
                       "ranking_status": ranking_status,
                       "source_ref": state["profile_raw_object_key"],
                       "content_hash": state["profile_sha256"],
                       "source_url": state.get("profile_url") or
                                     f"https://api.livetennisapi.com/api/public/v1/players/{source_id}",
                       "parser_version": state.get("profile_parser_version") or
                                         "live_tennis_player_profile_v2"})

            conn.execute(text("""
                UPDATE tennis_independent_metric_refresh_state
                SET processed_generation=:generation, updated_at=now()
                WHERE id=1
            """), {"generation": generation})
        return {"status": "materialized", "generation": generation,
                "dependency_digest": dependency_digest, "players_replayed": len(metrics),
                "confirmed_targets": len(targets), "snapshots_written": written,
                "available_at": cutoff.isoformat(), "pending_generation": current_generation > generation}
    finally:
        engine.dispose()
