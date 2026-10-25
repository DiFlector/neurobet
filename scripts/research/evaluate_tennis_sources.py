"""Evaluate archived HTTP probes without changing production identities or facts.

Run in the Research image with lxml installed. The input directory contains
*.body responses and request manifests from the source experiment; no API keys
are read or included in the report. Raw responses belong in MinIO, not git.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from lxml import html


def csv_rows(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace"))))


def tennisexplorer_results(body: bytes) -> list[dict]:
    """Parse singles in the main results table, including the unlinked Futures header."""
    tree = html.fromstring(body)
    tables = tree.xpath('//table[@class="result"][.//tr[contains(@class,"head")]]')
    if not tables:
        return []
    matches, section = [], None
    for tr in tables[0].xpath('.//tr'):
        if "head" in tr.get("class", "").split():
            cells = tr.xpath('./td[contains(@class,"t-name")]')
            section = None  # never inherit Challenger/ATP from a preceding section
            if not cells:
                continue
            label = cells[0].text_content().strip()
            hrefs = cells[0].xpath('.//a/@href')
            href = hrefs[0] if hrefs else ""
            low = label.lower()
            if "type=double" in href or any(x in low for x in ("utr ", "junior", "exhibition")):
                continue
            tour = ("itf" if "itf" in low or "futures" in low else
                    "challenger" if "challenger" in low else
                    "wta" if "/wta-women/" in href else
                    "atp" if "/atp-men/" in href else "unknown")
            section = {"tour": tour, "tournament": label, "tournament_url": href}
            continue
        if not section:
            continue
        first = tr.xpath('./td[contains(@class,"t-name")]/a[contains(@href,"/player/")]')
        match_links = tr.xpath('.//a[contains(@href,"match-detail")]/@href')
        other = tr.getnext()
        if not first or not match_links or other is None:
            continue
        second = other.xpath('./td[contains(@class,"t-name")]/a[contains(@href,"/player/")]')
        if not second:
            continue
        names = [first[0].text_content().strip(), second[0].text_content().strip()]
        if any("/" in n for n in names):
            continue
        sets, games = [], []
        for row in (tr, other):
            cells = row.xpath('./td[@class="result"]')
            sets.append(cells[0].text_content().strip() if cells else "")
            side = []
            for cell in row.xpath('./td[@class="score"]'):
                value = "".join(cell.xpath('.//text()[not(ancestor::sup)]')).strip()
                if value.isdigit():
                    side.append(int(value))
            games.append(side)
        mid = re.search(r"[?&]id=(\d+)", match_links[0])
        if not mid:
            continue
        matches.append({**section, "source_match_id": mid[1], "players": names,
                        "profiles": [first[0].get("href"), second[0].get("href")],
                        "sets": sets, "games": games})
    return matches


def reference_cohort(matches: list[dict]) -> list[dict]:
    result = []
    for tour in ("atp", "wta", "challenger", "itf"):
        eligible = [r for r in matches if r["tour"] == tour and
                    all(x.isdigit() for x in r["sets"]) and
                    max(int(x) for x in r["sets"]) in (2, 3) and
                    len(r["games"][0]) == len(r["games"][1]) == sum(int(x) for x in r["sets"])]
        # Include men's Futures as well as women's ITF rather than five from one event.
        if tour == "itf":
            women = [r for r in eligible if "/wta-women/" in r["tournament_url"]][:3]
            men = [r for r in eligible if r not in women and "futures" in r["tournament"].lower()][:2]
            chosen = women + men
            chosen += [r for r in eligible if r not in chosen][:5-len(chosen)]
        else:
            chosen = eligible[:5]
        result.extend(chosen)
    return result


def name_key(name: str) -> tuple[str, str]:
    """Candidate lookup only: surname tail and initial, never an identity assertion."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    parts = re.findall(r"[a-z]+", text)
    if len(parts) < 2:
        return (parts[0] if parts else "", "")
    if len(parts[0]) == 1:
        return parts[-1], parts[0]
    if len(parts[-1]) == 1:
        surname = [x for x in parts if len(x) > 1]
        return surname[-1] if surname else "", next(x for x in parts if len(x) == 1)
    return parts[-1], parts[0][0]


def pair_key(names: list[str]) -> tuple:
    return tuple(sorted(name_key(n) for n in names))


def score_pairs(score: str) -> list[list[int]]:
    return [[int(a), int(b)] for a, b in re.findall(r"(\d+)-(\d+)(?:\([^)]*\))?", score)]


def api_point_shape(event: dict, serve_points: int | None = None) -> dict:
    games = event.get("pointbypoint") or []
    points = sum(len(g.get("points") or []) for g in games)
    finals = {str(s.get("score_set")): [int(s["score_first"]), int(s["score_second"])]
              for s in event.get("scores", []) if str(s.get("score_first", "")).isdigit()
              and str(s.get("score_second", "")).isdigit()}
    last_scores = {}
    for game in games:
        if "tiebreak" in str(game.get("set_number", "")).lower():
            continue
        number = re.search(r"\d+", str(game.get("set_number", "")))
        score = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(game.get("score", "")))
        if number and score:
            last_scores[number[0]] = [int(score[1]), int(score[2])]
    result = {"raw_point_rows": points, "source_entries": len(games),
              "tiebreak_point_entries": sum("tiebreak" in str(g.get("set_number", "")).lower() for g in games),
              "game_end_scores_equal_final_sets": bool(finals) and finals == last_scores,
              "terminal_points_are_not_explicitly_certified": True}
    if serve_points is not None:
        result.update(serve_points_from_tml=serve_points,
                      raw_rows_equal_serve_points=points == serve_points,
                      naive_rows_plus_one_per_source_entry_equal_serve_points=points + len(games) == serve_points)
    return result


def tml_summary(rows: list[dict]) -> dict:
    return {"records": len(rows), "with_both_serve_points": sum(bool(r.get("w_svpt")) and bool(r.get("l_svpt")) for r in rows),
            "with_both_aces": sum(bool(r.get("w_ace")) and bool(r.get("l_ace")) for r in rows),
            "latest_provider_date": max((r.get("tourney_date", "") for r in rows), default=None),
            "date_precision": "provider_tournament_or_match_date_not_result_observation_time"}


def ta_summary(body: bytes, *, fragment=False) -> dict:
    text = body.decode("utf-8", errors="replace")
    if fragment:
        tree = html.fromstring(text)
        rows = tree.xpath('//table[@id="recent-results"]//tbody/tr')
        dates = []
        for row in rows:
            values = row.xpath('./td[1]/text()')
            if values:
                try:
                    dates.append(datetime.strptime(values[0].strip(), "%d-%b-%Y").date().isoformat())
                except ValueError:
                    pass
        return {"rows": len(rows), "latest_provider_date": max(dates, default=None),
                "stat_headers": [c.text_content().strip() for c in tree.xpath('//table[@id="recent-results"]//th')],
                "scope": "recent_results_not_full_career"}
    match = re.search(r"\bvar\s+matchmx\s*=\s*", text)
    rows = json.JSONDecoder().raw_decode(text[match.end():])[0] if match else []
    return {"rows": len(rows), "latest_provider_date": max((str(r[0]) for r in rows), default=None),
            "scope": "legacy_js_can_be_stale_do_not_guess_url"}


def evaluate(directory: Path) -> dict:
    manifest = []
    for pattern in ("initial-results.json", "detail-results.json", "third-results.json", "connection-results.json", "extra-results.json"):
        if (directory / pattern).exists():
            manifest.extend(json.loads((directory / pattern).read_text()))
    for pattern in ("candidate-result.json", "tournament-result.json", "worker-archive-result.json"):
        if (directory / pattern).exists():
            manifest.append(json.loads((directory / pattern).read_text()))
    for record in manifest:
        path = directory / (record["name"] + ".body")
        if record["name"].startswith("api_") and path.exists():
            payload = json.loads(path.read_bytes())
            record["provider_success"] = payload.get("success")
            record["provider_empty_without_result"] = payload.get("success") == 1 and "result" not in payload
    load = lambda n: (directory / (n + ".body")).read_bytes()
    matches = tennisexplorer_results(load("te_results"))
    cohort = reference_cohort(matches)
    tml, lookups = {}, {}
    for tour in ("atp", "wta", "challenger"):
        season, ongoing = csv_rows(load("tml_" + tour)), csv_rows(load("tml_ongoing_" + tour))
        tml[tour] = {"season": tml_summary(season), "ongoing": tml_summary(ongoing)}
        # De-duplicate unchanged files by source row identity, not just names.
        merged = {(r.get("tourney_id"), r.get("match_num"), r.get("winner_id")): r for r in season + ongoing}
        lookups[tour] = list(merged.values())
    candidates = {}
    for path in directory.glob("api_*.body"):
        if "events" in path.stem:
            continue
        try:
            data = json.loads(path.read_bytes()).get("result", [])
            for event in data:
                if isinstance(event, dict) and event.get("event_key") and event.get("event_date") == "2026-10-22":
                    candidates[str(event["event_key"])] = event
        except (ValueError, AttributeError):
            pass
    api = list(candidates.values())
    by_type = {}
    for kind in sorted({r.get("event_type_type", "unknown") for r in api}):
        records = [r for r in api if r.get("event_type_type", "unknown") == kind]
        by_type[kind] = {"records": len(records), "with_statistics": sum(bool(r.get("statistics")) for r in records),
                         "with_point_rows": sum(any(g.get("points") for g in r.get("pointbypoint", []) if isinstance(g, dict)) for r in records),
                         "point_completeness": "not_certified"}
    for match in cohort:
        candidates_tml = [r for r in lookups.get(match["tour"], []) if pair_key([r.get("winner_name", ""), r.get("loser_name", "")]) == pair_key(match["players"]) and "20261012" <= r.get("tourney_date", "") <= "20261022"]
        # The day page fixes the reference date; this name/date join is a candidate,
        # not an automatic confirmation of player IDs in the production database.
        for source, found in [("tml", candidates_tml), ("api_tennis", [r for r in api if pair_key([r.get("event_first_player", ""), r.get("event_second_player", "")]) == pair_key(match["players"])])]:
            comparison = {"candidate_count": len(found), "identity_confirmed": False}
            if len(found) == 1:
                row = found[0]
                if source == "tml":
                    games = score_pairs(row.get("score", "")); first = row.get("winner_name", "")
                    comparison.update(statistics=bool(row.get("w_svpt")) and bool(row.get("l_svpt")), source_player_ids=[row.get("winner_id"), row.get("loser_id")])
                else:
                    games = [[int(s["score_first"]), int(s["score_second"])] for s in row.get("scores", []) if str(s.get("score_first", "")).isdigit() and str(s.get("score_second", "")).isdigit()]
                    first = row.get("event_first_player", "")
                    comparison.update(statistics=bool(row.get("statistics")), source_match_id=str(row["event_key"]), status=row.get("event_status"))
                if name_key(first) != name_key(match["players"][0]):
                    games = [list(reversed(s)) for s in games]
                comparison["set_games_equal"] = games == [list(s) for s in zip(*match["games"])]
            match[source] = comparison
        if len(candidates_tml) == 1 and match["api_tennis"].get("candidate_count") == 1:
            event = next(r for r in api if str(r["event_key"]) == match["api_tennis"]["source_match_id"])
            row = candidates_tml[0]
            serve_points = (int(row["w_svpt"]) + int(row["l_svpt"])) if row.get("w_svpt", "").isdigit() and row.get("l_svpt", "").isdigit() else None
            match["api_pbp_shape"] = api_point_shape(event, serve_points)
            fields = []
            for player, suffix in [("event_first_player", "first_player_key"), ("event_second_player", "second_player_key")]:
                side = "w" if name_key(event[player]) == name_key(row["winner_name"]) else "l"
                for api_name, tml_name in [("Aces", "ace"), ("Double Faults", "df")]:
                    values = [x.get("stat_value") for x in event.get("statistics", []) if str(x.get("player_key")) == str(event[suffix]) and x.get("stat_period") == "match" and x.get("stat_name") == api_name]
                    if len(values) == 1 and row.get(f"{side}_{tml_name}") not in (None, ""):
                        fields.append({"player": event[player], "metric": api_name, "api_tennis": values[0],
                                       "tml": row[f"{side}_{tml_name}"], "equal": str(values[0]) == str(row[f"{side}_{tml_name}"])})
            match["statistics_comparison"] = fields
        elif match["api_tennis"].get("candidate_count") == 1:
            event = next(r for r in api if str(r["event_key"]) == match["api_tennis"]["source_match_id"])
            match["api_pbp_shape"] = api_point_shape(event)
    ta = {p.stem: ta_summary(p.read_bytes(), fragment="frag" in p.stem) for p in directory.glob("ta_*.body") if "page" not in p.stem}
    profile_pages = {}
    for path in directory.glob("te_profile_*.body"):
        tree = html.fromstring(path.read_bytes())
        profile_pages[path.stem] = {"headings": tree.xpath('//h1/text()|//h2/text()|//h3/text()')[:3],
                                   "match_links": len(set(tree.xpath('//a[contains(@href,"match-detail")]/@href'))),
                                   "scope": "one_profile_page_not_certified_full_career"}
    live = json.loads(load("live_api")).get("data", [])
    live_summary = {"catalog_matches": len(live), "by_tour": dict(Counter(r.get("tour") or "unknown" for r in live)),
                    "with_format_field": sum(bool(r.get("format")) for r in live),
                    "format_source_basis_verified": False,
                    "statistics_error": json.loads(load("live_stats")).get("error"),
                    "points_error": json.loads(load("live_points")).get("error")}
    stages = json.loads(load("livescore")).get("Stages", [])
    live_score_summary = {"stages": len(stages), "events": sum(len(s.get("Events", [])) for s in stages),
                          "category_names": sorted({s.get("Cnm") for s in stages if s.get("Cnm")}),
                          "statistics_or_full_point_tape_verified": False}
    mcp = {}
    for sex in ("m", "w"):
        matches_path, serve_path = directory / f"mcp_{sex}_matches.body", directory / f"mcp_{sex}_serve.body"
        if matches_path.exists() and serve_path.exists():
            metadata, serve = csv_rows(matches_path.read_bytes()), csv_rows(serve_path.read_bytes())
            totals = [r for r in serve if r.get("row") == "Total"]
            counts = Counter(r.get("match_id") for r in totals)
            valid_dates = [r.get("Date", "") for r in metadata if re.fullmatch(r"\d{8}", r.get("Date", ""))]
            mcp[sex] = {"metadata_matches": len(metadata), "serve_total_rows": len(totals),
                        "matches_with_two_serve_rows": sum(n == 2 for n in counts.values()),
                        "latest_provider_date": max(valid_dates, default=None), "invalid_date_rows": len(metadata) - len(valid_dates),
                        "scope": "selected_charted_matches_not_a_complete_tour"}
    return {"schema_version": "1.0", "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "usage": {"purpose": "noncommercial_research", "rights_review": "user_confirmed_2026-10-23"},
            "request_probes": manifest, "raw_archive": json.loads((directory / "archive-index.json").read_text()) if (directory / "archive-index.json").exists() else {},
            "tennisexplorer": {"singles_by_tour": dict(Counter(r["tour"] for r in matches)), "profile_pages": profile_pages},
            "tennis_my_life": tml, "api_tennis": {"unique_records": len(api), "by_event_type": by_type},
            "tennis_abstract": ta, "match_charting_project": mcp, "cohort": cohort,
            "live_tennis_api_free": live_summary, "livescore": live_score_summary,
            "limits": ["20 completed candidates from TennisExplorer 2026-10-22, five per tour; not an independent official score audit",
                       "Name/date/score concordance does not confirm production player identity",
                       "No source receives complete-PBP or point-in-time certification from these probes",
                       "Single-request latency is not a paired throughput benchmark or a 24-hour reliability result"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args.input_dir)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"cohort": len(report["cohort"]), "tml": report["tennis_my_life"], "api_tennis": report["api_tennis"]}, ensure_ascii=False))
