"""Conservative parser and transition validation for archived tennis point history."""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

PARSER_VERSION = "flashscore-point-history-v7"


def is_authoritative_point_history_version(events: Any, quality: Any) -> bool:
    """Whether a parsed document may replace the current sequence for its set.

    A page that lacks the PBP tab, failed to load, or contains no recognizable
    history is not an empty correction. It must not erase a previously parsed
    set. An explicit, versioned empty correction is the only empty payload
    that intentionally acts as a tombstone.
    """
    quality = quality if isinstance(quality, dict) else {}
    if quality.get("explicit_empty_correction") is True:
        return True
    if isinstance(events, list) and events:
        return True
    if quality.get("set_tab_confirmed") is False or quality.get("fetch_status"):
        return False
    gaps = quality.get("gaps") or []
    reasons = {
        "point_by_point_markup_missing",
        "HISTORY_EMPTY_OR_UNRECOGNIZED",
        "set_tabs_not_discovered",
    }
    for gap in gaps:
        reason = gap.get("reason") if isinstance(gap, dict) else gap
        if reason in reasons:
            return False
    return bool(quality.get("complete"))


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
    if isinstance(node, _Node):
        yield node
        for child in node.children:
            yield from _walk(child)
        return
    # lxml performs tree construction in C; use its iterator rather than
    # materializing a Python node object for every element in the full page.
    yield from node.iter()


def _walk_with_parent(node, parent=None):
    if not isinstance(node, _Node):
        for child in node.iter():
            yield child, child.getparent()
        return
    yield node, parent
    for child in node.children:
        yield from _walk_with_parent(child, node)


def _attrs(node): return node.attrs if isinstance(node, _Node) else (getattr(node, "attrib", None) or {})
def _children(node): return node.children if isinstance(node, _Node) else list(node)
def _own_text(node):
    if isinstance(node, _Node):
        parts = getattr(node, "text_parts", None)
        return "".join(parts) if parts is not None else node.text
    return node.text or ""
def _classes(node): return set(_attrs(node).get("class", "").split())
def _text(node):
    if not isinstance(node, _Node):
        return " ".join(" ".join(node.itertext()).split())
    return " ".join((_own_text(node) + " " + " ".join(_text(x) for x in node.children)).split())
def _has(node, cls): return cls in _classes(node)


def _server_side(row_nodes):
    """Return the side marked as serving by Flashscore's active server marker.

    Rows contain a placeholder ``__servis`` node for both participants. Only
    the active marker carries ``__highlight``; taking the last placeholder
    silently classified every point as the away player's serve.
    """
    highlighted = []
    for node in row_nodes:
        classes = _classes(node)
        if "matchHistoryRow__servis" not in classes or "matchHistoryRow__highlight" not in classes:
            continue
        if "matchHistoryRow__home" in classes:
            highlighted.append(0)
        elif "matchHistoryRow__away" in classes:
            highlighted.append(1)
    return highlighted[0] if len(highlighted) == 1 else None


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
    try:
        from lxml import html as lxml_html
        root = lxml_html.fromstring(html)
    except ImportError:
        parser = _TreeParser(); parser.feed(html)
        root = parser.root
    except Exception:
        # A malformed or unusual source document must not become empty history
        # merely because the optional fast backend rejects its document tree.
        parser = _TreeParser(); parser.feed(html)
        root = parser.root
    nodes_with_parents = list(_walk_with_parent(root))
    headers = [(node, parent) for node, parent in nodes_with_parents
               if _has(node, "sectionHeader") and parent is not None]
    events, gaps, sets_seen, tb_previous, prior_game_score = [], [], set(), {}, {}
    for header, parent in headers:
        header_text = _text(header)
        sm = re.search(r"(?:\bset\s*(\d+)|(\d+)\s*[-–]?\s*(?:й\s*)?(?:set|сет))", header_text, re.I)
        set_no = int(sm.group(1) or sm.group(2)) if sm else None
        is_tiebreak = bool(re.search(r"tie[\s-]*break|тай[\s-]*брейк|тайбрейк", header_text, re.I))
        if set_no is not None: sets_seen.add(set_no)
        siblings = _children(parent)
        header_index = siblings.index(header)
        children = []
        for sibling in siblings[header_index + 1:]:
            if _has(sibling, "sectionHeader"):
                break
            children.append(sibling)
        for index, row in enumerate(children):
            if not _has(row, "matchHistoryRow"): continue
            row_nodes = list(_walk(row))
            winning_spans = [n for n in row_nodes if _attrs(n).get("data-testid") == "wcl-pointByPointScore"]
            if is_tiebreak and len(winning_spans) >= 2:
                values = [re.sub(r"\D", "", _text(n)) for n in winning_spans[:2]]
                if all(values):
                    after = [int(values[0]), int(values[1])]
                    before = tb_previous.get(set_no, [0, 0])
                    changed = [i for i in (0, 1) if after[i] == before[i] + 1 and after[1-i] == before[1-i]]
                    marker = next((i for i, n in enumerate(winning_spans[:2]) if _attrs(n).get("data-winning") == "true"), None)
                    winner = changed[0] if len(changed) == 1 and marker == changed[0] else None
                    side_server = _server_side(row_nodes)
                    if winner is None:
                        gaps.append({"set": set_no, "tie_break_score": after, "reason": "invalid_or_unanchored_tiebreak_transition"})
                    events.append({"set": set_no, "game_score": [6, 6], "tie_break": True,
                                   "state_before": before, "state_after": after,
                                   "point_score": after,
                                   "server": side_server, "winner": winner, "confirmed": winner is not None,
                                   "retrospective": True, "quality": "confirmed" if winner is not None else "ambiguous"})
                    tb_previous[set_no] = after
                continue
            scorebox = next((n for n in row_nodes if _has(n, "matchHistoryRow__scoreBox")), None)
            spans = [n for n in _walk(scorebox) if n.tag == "span"] if scorebox is not None else []
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
            side_server = _server_side(row_nodes)
            following = children[index+1] if index+1 < len(children) else None
            point_nodes = ([n for n in _walk(following)]
                           if following is not None and _has(following, "matchHistoryRow__fifteens") else [])
            point_states = []
            for n in point_nodes:
                if _has(n, "matchHistoryRow__fifteen"):
                    # Remove BP/MP badge text by extracting only text before its child badges.
                    raw = _own_text(n).strip() or _text(n)
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
                if result_node is not None:
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
    # Point-by-point feeds often omit a separate GAME row for the tiebreak.
    # Once a confirmed numeric sequence reaches a legal terminal score, retain
    # an explicit set result so downstream set/game labels do not mistake 6:6
    # for the completed score. The terminal marker is not itself a point.
    for set_no, score in tb_previous.items():
        if max(score) < 7 or abs(score[0] - score[1]) < 2:
            continue
        last = next((item for item in reversed(events)
                     if item.get("set") == set_no and item.get("tie_break")
                     and item.get("quality") == "confirmed"), None)
        if last is None or last.get("state_after") != score:
            continue
        winner = 0 if score[0] > score[1] else 1
        set_score = [7, 6] if winner == 0 else [6, 7]
        events.append({"set": set_no, "game_score": set_score, "point_index": None,
                       "state_before": list(score), "state_after": {"set_result": set_score},
                       "point_score": None, "server": last.get("server"),
                       "winner": winner, "confirmed": True, "tie_break": True,
                       "retrospective": True, "quality": "terminal_from_tiebreak_result"})
    if not headers: gaps.append({"reason": "point_by_point_markup_missing"})
    return {"parser_version": PARSER_VERSION, "events": events, "sets": sorted(sets_seen),
            "quality": {"event_count": len(events), "confirmed_points": sum(
                            bool(e["confirmed"]) and e.get("quality") in {"confirmed", "terminal_from_game_result"}
                            for e in events),
                        "ambiguous_points": sum(not e["confirmed"] for e in events), "gaps": gaps,
                        "complete": bool(events) and not gaps}}
