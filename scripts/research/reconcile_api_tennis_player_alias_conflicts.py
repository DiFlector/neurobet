#!/usr/bin/env python3
"""Dry-run or auditably reconcile stale API-Tennis player aliases.

The plan is restricted to IDs for which the existing canonical projection
resolver uniquely chooses the same established player as the Fonbet identity
proofs in the linked match. Ambiguous API-only cards stay unresolved.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid

from sqlalchemy import select

from db.connection import SessionLocal
from db.models.external_tennis import (
    ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer,
    ExtTennisCanonicalPlayerAlias, ExtTennisMatch,
    ExtTennisMatchObservation,
)
from app.canonical_player_aliases import (
    confirm_reviewed_player_alias, reassign_confirmed_player_alias,
)
from app.canonical_tennis_projection import (
    _player_key, _score_key, resolve_api_tennis_player_identity,
)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def _api_player_resolution(session, provider_id, name, gender):
    key = _player_key(name, gender)
    exact = session.execute(select(ExtTennisCanonicalPlayer).where(
        ExtTennisCanonicalPlayer.source_ids["api_tennis"].astext == str(provider_id)
    )).scalars().all()
    named = session.execute(select(ExtTennisCanonicalPlayer).where(
        ExtTennisCanonicalPlayer.surname_key == key[0]
    )).scalars().all()
    peers = [row for row in named if (row.surname_key.casefold(), row.initial.casefold(), row.gender.casefold()) == key]
    if not peers and key[2] != "unknown":
        peers = [row for row in named if (row.surname_key.casefold(), row.initial.casefold(),
                                          row.gender.casefold()) == (key[0], key[1], "unknown")]
    return resolve_api_tennis_player_identity(
        str(provider_id), key, exact_candidates=exact, peer_candidates=peers, named_people=named)


def _event_reference(session, row, as_of):
    evidence = row.get("evidence") or {}
    check = evidence.get("provider_identity_check") or {}
    canonical = session.get(ExtTennisCanonicalMatch, evidence.get("canonical_match_id"))
    source_match_id = check.get("provider_match_id")
    provider = session.query(ExtTennisMatch).filter(
        ExtTennisMatch.source == "api_tennis",
        ExtTennisMatch.source_match_id == source_match_id,
        ExtTennisMatch.available_at <= as_of,
    ).first()
    if canonical is None or provider is None:
        return None
    expected = {str(value) for value in check.get("expected_canonical_pair", [])}
    if expected != {str(canonical.winner_player_id), str(canonical.loser_player_id)}:
        return None
    if (str(provider.winner_player_id), str(provider.loser_player_id)) != tuple(
            str(value) for value in check.get("provider_winner_loser_ids", [])):
        return None
    observation_id = check.get("source_observation_id")
    observation = session.get(ExtTennisMatchObservation, uuid.UUID(observation_id)) if observation_id else None
    raw = (observation.payload or {}).get("event") if observation else None
    if (not isinstance(raw, dict)
            or {str(raw.get("first_player_key")), str(raw.get("second_player_key"))}
            != {str(provider.winner_player_id), str(provider.loser_player_id)}
            or {"first player": str(raw.get("first_player_key")),
                "second player": str(raw.get("second_player_key"))}.get(
                    str(raw.get("event_winner", "")).casefold()) != str(provider.winner_player_id)
            or observation.available_at > as_of
            or _score_key(provider.score) == ()
            or _score_key(provider.score) != _score_key(canonical.score)):
        return None

    independent = []
    for source_ref in (canonical.stats or {}).get("sources", []):
        if not isinstance(source_ref, str) or ":" not in source_ref:
            continue
        source, other_id = source_ref.split(":", 1)
        if source == "api_tennis":
            continue
        other = session.query(ExtTennisMatch).filter(
            ExtTennisMatch.source == source,
            ExtTennisMatch.source_match_id == other_id,
        ).first()
        if other is None or _score_key(other.score) != _score_key(provider.score):
            continue
        if abs((other.played_at - provider.played_at).total_seconds()) > 86400:
            continue
        ids = [str(other.winner_player_id or ""), str(other.loser_player_id or "")]
        aliases = session.query(ExtTennisCanonicalPlayerAlias).filter(
            ExtTennisCanonicalPlayerAlias.source == source,
            ExtTennisCanonicalPlayerAlias.source_player_id.in_(ids),
            ExtTennisCanonicalPlayerAlias.status == "confirmed",
        ).all()
        mapped = {alias.source_player_id: str(alias.canonical_player_id) for alias in aliases}
        if (len(mapped) == 2 and set(mapped.values()) == expected
                and mapped.get(ids[0]) == str(canonical.winner_player_id)
                and mapped.get(ids[1]) == str(canonical.loser_player_id)):
            independent.append({
                "source": source, "source_match_id": other_id,
                "winner_player_id": ids[0], "loser_player_id": ids[1],
                "winner_name": other.winner_name, "loser_name": other.loser_name,
                "played_at": other.played_at.isoformat(), "score": other.score,
                "canonical_player_ids": [mapped[ids[0]], mapped[ids[1]]],
            })

    identity_proofs = evidence.get("canonical_identity_proofs") or []
    target_by_api_id = {
        str(provider.winner_player_id): str(canonical.winner_player_id),
        str(provider.loser_player_id): str(canonical.loser_player_id),
    }
    resolution_by_api_id = {}
    for provider_id, name in ((str(provider.winner_player_id), provider.winner_name),
                              (str(provider.loser_player_id), provider.loser_name)):
        person, proof = _api_player_resolution(session, provider_id, name, provider.gender)
        resolution_by_api_id[provider_id] = {
            "canonical_player_id": str(person.id) if person is not None else None,
            "proof": proof,
        }
    if len(identity_proofs) != 2 or {x.get("canonical_id") for x in identity_proofs} != expected:
        return None
    return {
        "fonbet_event_id": str(row.get("fonbet_source_event_id")),
        "fonbet_event_uuid": str(row.get("event_id")),
        "participants": row.get("participants"),
        "participant_identity_proofs": identity_proofs,
        "api_match_id": str(provider.source_match_id),
        "api_observation_id": str(observation.id),
        "api_observation_available_at": observation.available_at.isoformat(),
        "api_source_content_hash": observation.source_content_hash,
        "api_match": {
            "winner_player_id": str(provider.winner_player_id),
            "loser_player_id": str(provider.loser_player_id),
            "winner_name": provider.winner_name, "loser_name": provider.loser_name,
            "played_at": provider.played_at.isoformat(), "score": provider.score,
        },
        "canonical_match_id": str(canonical.id), "canonical_key": canonical.canonical_key,
        "canonical_match_sources": sorted((canonical.stats or {}).get("sources", [])),
        "independent_same_pair_matches": independent,
        "target_by_api_id": target_by_api_id,
        "resolution_by_api_id": resolution_by_api_id,
    }


def build_plan(link_report_path):
    link_path = Path(link_report_path).resolve()
    report = json.loads(link_path.read_text())
    if report.get("status") != "complete":
        raise ValueError("link_candidate_report_incomplete")
    conflicts = [row for row in report.get("rows", [])
                 if row.get("reason") == "provider_player_ids_unconfirmed_or_conflicting"]
    references = defaultdict(list)
    rejected_events = []
    with SessionLocal() as session:
        as_of = datetime.fromisoformat(report["as_of"].replace("Z", "+00:00"))
        for row in conflicts:
            reference = _event_reference(session, row, as_of)
            if reference is None:
                rejected_events.append({"fonbet_source_event_id": row.get("fonbet_source_event_id"),
                                        "reason": "independent_match_evidence_incomplete"})
                continue
            for provider_id, target in reference["target_by_api_id"].items():
                resolution = reference["resolution_by_api_id"][provider_id]
                proof = resolution["proof"]
                if (resolution["canonical_player_id"] != target
                        or proof.get("method") not in {
                            "exact_api_id_plus_unique_established_peer", "exact_api_id_only",
                            "unique_non_api_profile"}):
                    rejected_events.append({"fonbet_source_event_id": row.get("fonbet_source_event_id"),
                                            "api_player_id": provider_id,
                                            "reason": "projection_resolver_disagrees_with_confirmed_match_identity"})
                    continue
                references[provider_id].append({
                    "target_canonical_player_id": target,
                    "resolver_method": proof.get("method"),
                    "resolver_proof": proof,
                    "match_reference": reference,
                })

        plan_rows = []
        unsafe = []
        for provider_id, refs in sorted(references.items()):
            targets = {ref["target_canonical_player_id"] for ref in refs}
            resolved = {ref["resolver_proof"].get("selected_card_id")
                        or ref["resolver_proof"].get("api_card_id")
                        for ref in refs}
            methods = {ref["resolver_method"] for ref in refs}
            if len(targets) != 1 or len(resolved) != 1 or targets != resolved:
                unsafe.append({"source_player_id": provider_id, "reason": "identity_mapping_not_unique",
                               "targets": sorted(targets), "resolved": sorted(str(x) for x in resolved)})
                continue
            target_id = uuid.UUID(next(iter(targets)))
            aliases = session.query(ExtTennisCanonicalPlayerAlias).filter(
                ExtTennisCanonicalPlayerAlias.source == "api_tennis",
                ExtTennisCanonicalPlayerAlias.source_player_id == provider_id,
            ).all()
            confirmed = [alias for alias in aliases if alias.status == "confirmed"]
            if len(confirmed) > 1:
                unsafe.append({"source_player_id": provider_id, "reason": "multiple_confirmed_owners"})
                continue
            current = str(confirmed[0].canonical_player_id) if confirmed else None
            if current == str(target_id):
                action = "already_correct"
            elif current is not None:
                action = "reassign"
            else:
                action = "confirm"
            if ("exact_api_id_only" in methods and current != str(target_id)
                    and any(ref["resolver_proof"].get("api_card_id") != str(target_id) for ref in refs)):
                unsafe.append({"source_player_id": provider_id,
                               "reason": "no_independent_established_identity_peer"})
                continue
            rationale = (
                "The existing API-Tennis projection resolver uniquely selected the same established canonical "
                "player as the confirmed Fonbet participant identity. The assignment is consistent across all "
                "reviewed matches and API raw winner/loser IDs; match-level evidence and independent provider "
                "aliases are attached."
            )
            evidence = {
                "method": "reviewed_api_tennis_alias_reconciliation_v1",
                "projection_methods": sorted(methods),
                "source_player_id": provider_id,
                "selected_canonical_player_id": str(target_id),
                "current_confirmed_owner_id": current,
                "reviewed_match_count": len(refs),
                "independent_source_match_count": sum(bool(ref["match_reference"]["independent_same_pair_matches"])
                                                       for ref in refs),
                "match_references": [ref["match_reference"] for ref in refs],
            }
            plan_rows.append({
                "source": "api_tennis", "source_player_id": provider_id,
                "canonical_player_id": str(target_id), "current_owner_id": current,
                "action": action, "rationale": rationale, "evidence": evidence,
                "evidence_digest": _sha(evidence),
            })

    plan = {
        "status": "complete", "generated_at": datetime.now().astimezone().isoformat(),
        "link_dry_run_path": str(link_path),
        "link_dry_run_sha256": hashlib.sha256(link_path.read_bytes()).hexdigest(),
        "reviewed_conflict_events": len(conflicts),
        "unique_provider_ids": len(references), "actions": plan_rows,
        "counts": {action: sum(row["action"] == action for row in plan_rows)
                   for action in ("reassign", "confirm", "already_correct")},
        "unsafe": unsafe, "rejected_events": rejected_events,
        "training_gate": "blocked_not_an_admission_report",
    }
    return plan


def apply_plan(plan_path):
    plan_file = Path(plan_path).resolve()
    plan = json.loads(plan_file.read_text())
    current_input = Path(plan["link_dry_run_path"])
    if not current_input.exists() or hashlib.sha256(current_input.read_bytes()).hexdigest() != plan["link_dry_run_sha256"]:
        raise ValueError("link_candidate_report_changed_since_plan")
    result = {"status": "complete", "mode": "apply", "plan_sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(),
              "rows": [], "counts": defaultdict(int)}
    for item in plan.get("actions", []):
        with SessionLocal() as session:
            aliases = session.query(ExtTennisCanonicalPlayerAlias).filter(
                ExtTennisCanonicalPlayerAlias.source == item["source"],
                ExtTennisCanonicalPlayerAlias.source_player_id == item["source_player_id"],
            ).with_for_update().all()
            confirmed = [alias for alias in aliases if alias.status == "confirmed"]
            current = str(confirmed[0].canonical_player_id) if len(confirmed) == 1 else None
            target = uuid.UUID(item["canonical_player_id"])
            if len(confirmed) > 1:
                raise ValueError("provider_alias_has_multiple_confirmed_owners")
            if current == str(target):
                result["counts"]["already_correct"] += 1
                result["rows"].append({"source_player_id": item["source_player_id"], "action": "already_correct"})
                session.rollback()
                continue
            if current != item.get("current_owner_id"):
                raise ValueError(f"provider_alias_owner_changed:{item['source_player_id']}")
            if item["action"] == "reassign":
                if current is None:
                    raise ValueError("expected_current_owner_missing")
                decision = reassign_confirmed_player_alias(
                    session, source=item["source"], source_player_id=item["source_player_id"],
                    expected_current_owner_id=uuid.UUID(current), canonical_player_id=target,
                    rationale=item["rationale"], evidence=item["evidence"], decided_by="codex_user_directed",
                )
            elif item["action"] == "confirm":
                decision = confirm_reviewed_player_alias(
                    session, source=item["source"], source_player_id=item["source_player_id"],
                    canonical_player_id=target, rationale=item["rationale"],
                    evidence=item["evidence"], decided_by="codex_user_directed",
                )
            else:
                raise ValueError("unexpected_alias_action")
            session.commit()
            result["counts"][item["action"]] += 1
            result["rows"].append({
                "source_player_id": item["source_player_id"], "action": item["action"],
                "canonical_player_id": str(target),
                "decision_id": str(decision.id) if decision else None,
                "evidence_digest": item["evidence_digest"],
            })
    result["counts"] = dict(result["counts"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--link-dry-run", help="Completed link candidate report; default mode is dry-run")
    parser.add_argument("--output", required=True)
    parser.add_argument("--apply-plan", help="Apply an unchanged reviewed plan produced by a previous dry-run")
    args = parser.parse_args()
    if bool(args.link_dry_run) == bool(args.apply_plan):
        parser.error("provide exactly one of --link-dry-run or --apply-plan")
    result = apply_plan(args.apply_plan) if args.apply_plan else build_plan(args.link_dry_run)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=dict) + "\n"
    Path(args.output).write_text(rendered, encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"actions", "rows"}},
                     ensure_ascii=False, sort_keys=True, default=dict))


if __name__ == "__main__":
    main()
