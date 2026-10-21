"""Conservative parser and transition validation for archived tennis point history."""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

PARSER_VERSION = "flashscore-point-history-v2"


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag, self.attrs, self.children, self.text = tag, attrs, [], ""


class _TreeParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs)); self.stack[-1].children.append(node)
        if tag not in self.VOID: self.stack.append(node)
    def handle_endtag(self, tag):
        for idx in range(len(self.stack)-1, 0, -1):
            if self.stack[idx].tag == tag:
                del self.stack[idx:]; break
    def handle_data(self, data): self.stack[-1].text += data


def _walk(node):
    yield node
    for child in node.children: yield from _walk(child)


def _walk_with_parent(node, parent=None):
    yield node, parent
    for child in node.children:
        yield from _walk_with_parent(child, node)


def _classes(node): return set(node.attrs.get("class", "").split())
def _text(node): return " ".join((node.text + " " + " ".join(_text(x) for x in node.children)).split())
def _has(node, cls): return cls in _classes(node)


def _point_pair(value: str):
    match = re.search(r"(?<!\d)(0|15|30|40|A|AD)\s*[:\-]\s*(0|15|30|40|A|AD)(?!\d)", value.upper())
    if not match: return None
    return tuple("AD" if x == "A" else x for x in match.groups())


def is_legal_point_state(a: int, b: int, tie_break: bool = False) -> bool:
    """Shared score-state guard used by the source parser and forecast parser."""
    if tie_break:
        return isinstance(a, int) and isinstance(b, int) and a >= 0 and b >= 0
    if not isinstance(a, int) or not isinstance(b, int) or min(a, b) < 0:
        return False
    if max(a, b) <= 2 or (a == 3 and b <= 3) or (b == 3 and a <= 3):
        return True
    return (a == 4 and b == 3) or (a == 3 and b == 4)


def infer_point_winner(before, after):
    """Return a side only when one legal regular-game point transition explains both scores."""
    if before is None or after is None: return None
    if not all(is_legal_point_state(_point_order(x), _point_order(y)) for x, y in (before, after)):
        return None
    order = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4}
    changed = [i for i in (0, 1) if before[i] != after[i]]
    if len(changed) != 1: return None
    side = changed[0]; other = 1-side
    if before[side] == "40" and after[side] == "AD" and before[other] == "40": return side
    if before[side] == "AD" and after[side] == "40" and before[other] == "40": return other
    if before[side] in order and after[side] in order and order[after[side]] == order[before[side]] + 1:
        return side
    return None


def _point_order(value):
    return {"0":0, "15":1, "30":2, "40":3, "AD":4}.get(str(value).upper(), -1)


def parse_point_history_html(html: str) -> dict[str, Any]:
    """Parse visible Flashscore PBP markup. Ambiguous outcomes remain unlabeled."""
    parser = _TreeParser(); parser.feed(html)
    nodes_with_parents = list(_walk_with_parent(parser.root))
    headers = [(node, parent) for node, parent in nodes_with_parents
               if _has(node, "sectionHeader") and parent is not None]
    events, gaps, sets_seen, tb_previous, prior_game_score = [], [], set(), {}, {}
    for header, parent in headers:
        header_text = _text(header)
        sm = re.search(r"(?:\bset\s*(\d+)|(\d+)\s*[-–]?\s*(?:й\s*)?(?:set|сет))", header_text, re.I)
        set_no = int(sm.group(1) or sm.group(2)) if sm else None
        is_tiebreak = bool(re.search(r"tie[\s-]*break|тай[\s-]*брейк|тайбрейк", header_text, re.I))
        if set_no is not None: sets_seen.add(set_no)
        siblings = parent.children
        header_index = siblings.index(header)
        children = []
        for sibling in siblings[header_index + 1:]:
            if _has(sibling, "sectionHeader"):
                break
            children.append(sibling)
        for index, row in enumerate(children):
            if not _has(row, "matchHistoryRow"): continue
            row_nodes = list(_walk(row))
            winning_spans = [n for n in row_nodes if n.attrs.get("data-testid") == "wcl-pointByPointScore"]
            if is_tiebreak and len(winning_spans) >= 2:
                values = [re.sub(r"\D", "", _text(n)) for n in winning_spans[:2]]
                if all(values):
                    after = [int(values[0]), int(values[1])]
                    before = tb_previous.get(set_no, [0, 0])
                    changed = [i for i in (0, 1) if after[i] == before[i] + 1 and after[1-i] == before[1-i]]
                    marker = next((i for i, n in enumerate(winning_spans[:2]) if n.attrs.get("data-winning") == "true"), None)
                    winner = changed[0] if len(changed) == 1 and marker == changed[0] else None
                    side_server = None
                    for n in row_nodes:
                        classes = _classes(n)
                        if "matchHistoryRow__servis" in classes: side_server = 0 if "matchHistoryRow__home" in classes else 1
                    if winner is None:
                        gaps.append({"set": set_no, "tie_break_score": after, "reason": "invalid_or_unanchored_tiebreak_transition"})
                    events.append({"set": set_no, "tie_break": True, "state_before": before, "state_after": after,
                                   "point_score": after,
                                   "server": side_server, "winner": winner, "confirmed": winner is not None,
                                   "retrospective": True, "quality": "confirmed" if winner is not None else "ambiguous"})
                    tb_previous[set_no] = after
                continue
            scorebox = next((n for n in row_nodes if _has(n, "matchHistoryRow__scoreBox")), None)
            spans = [n for n in _walk(scorebox) if n.tag == "span"] if scorebox else []
            # Set-summary rows can include superscript tiebreak scores (e.g.
            # 6⁴–7⁷). They are not per-game states and must not be flattened
            # into invalid scores such as 64–77.
            if len(spans) != 2 or any(n.tag == "sup" for n in _walk(scorebox)):
                continue
            raw_scores = [re.search(r"\d+", _text(n)).group(0) if re.search(r"\d+", _text(n)) else ""
                          for n in spans]
            if not all(raw_scores): continue
            game_score = [int(raw_scores[0]), int(raw_scores[1])]
            previous_game_score = prior_game_score.get(set_no, [0, 0])
            changed_games = [i for i in (0, 1) if game_score[i] == previous_game_score[i] + 1 and game_score[1-i] == previous_game_score[1-i]]
            finished_game_side = changed_games[0] if len(changed_games) == 1 else None
            side_server = None
            for n in row_nodes:
                classes = _classes(n)
                if "matchHistoryRow__servis" in classes:
                    side_server = 0 if "matchHistoryRow__home" in classes else 1
            following = children[index+1] if index+1 < len(children) else None
            point_nodes = [n for n in _walk(following)] if following and _has(following, "matchHistoryRow__fifteens") else []
            point_states = []
            for n in point_nodes:
                if _has(n, "matchHistoryRow__fifteen"):
                    # Remove BP/MP badge text by extracting only text before its child badges.
                    raw = n.text.strip() or _text(n)
                    pair = _point_pair(raw)
                    if pair: point_states.append(pair)
            previous = ("0", "0")
            for point_index, state in enumerate(point_states):
                winner = infer_point_winner(previous, state)
                events.append({"set": set_no, "game_score": game_score, "point_index": point_index,
                               "state_before": list(previous), "state_after": list(state),
                               "point_score": list(state), "server": side_server,
                               "winner": winner, "confirmed": winner is not None,
                               "retrospective": True, "quality": "confirmed" if winner is not None else "ambiguous"})
                previous = state
            if point_states:
                result_node = next((n for n in _walk(following) if _has(n, "matchHistoryRow__gameResult")), None)
                if result_node:
                    # A normal game result adds a terminal point only when the observed score uniquely permits it.
                    possible = []
                    for side in (0, 1):
                        if previous[side] == "AD" or (previous[side] == "40" and previous[1-side] in {"0", "15", "30"}):
                            possible.append(side)
                    if len(possible) == 1 and finished_game_side == possible[0]:
                        events.append({"set": set_no, "game_score": game_score, "point_index": len(point_states),
                                       "state_before": list(previous), "state_after": {"game_result": game_score},
                                       "point_score": None, "server": side_server, "winner": possible[0],
                                       "confirmed": True, "retrospective": True, "quality": "terminal_from_game_result"})
                    else: gaps.append({"set": set_no, "game_score": game_score, "reason": "ambiguous_terminal_point"})
                else:
                    gaps.append({"set": set_no, "game_score": game_score, "reason": "game_result_missing"})
            else:
                gaps.append({"set": set_no, "game_score": game_score, "reason": "point_sequence_missing"})
            if len(changed_games) != 1:
                gaps.append({"set": set_no, "game_score": game_score, "reason": "game_score_transition_missing_or_invalid"})
            prior_game_score[set_no] = game_score
    if not headers: gaps.append({"reason": "point_by_point_markup_missing"})
    return {"parser_version": PARSER_VERSION, "events": events, "sets": sorted(sets_seen),
            "quality": {"event_count": len(events), "confirmed_points": sum(bool(e["confirmed"]) for e in events),
                        "ambiguous_points": sum(not e["confirmed"] for e in events), "gaps": gaps,
                        "complete": bool(events) and not gaps}}
