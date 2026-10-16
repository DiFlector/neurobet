"""One composite decision from three independent tennis lines.

Each line confirms its own side on two consecutive snapshots. The composite
selects that side only when all three confirmed lines name the same one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

COMPONENT_ORDER = ("logistic", "boosting", "residual_mlp")
COMPONENT_LABELS = {
    "logistic": "Логистическая",
    "boosting": "Бустинг",
    "residual_mlp": "Residual MLP 64→32",
}
DISPLAY_LABEL = "Составная модель"


def phase_text(agreement_count: int, selected: bool) -> str:
    """Public phase: 0/3, 1/3, 2/3, or the selected state."""
    if selected:
        return "Выбрано"
    return f"{int(agreement_count)}/3"


def advance_components(
    previous: Optional[List[Dict[str, Any]]],
    current: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Move each line by one snapshot and count how many now agree."""
    prior = {str(item.get("id")): item for item in (previous or [])}
    components: List[Dict[str, Any]] = []
    for item in current:
        component_id = str(item["id"])
        side = item.get("side") or None
        earlier = prior.get(component_id) or {}
        previous_side = earlier.get("candidate_side") or None
        confirmed = bool(side and previous_side and side == previous_side)
        components.append(
            {
                **item,
                "id": component_id,
                "label": item.get("label") or COMPONENT_LABELS.get(component_id, component_id),
                "side": side,
                "candidate_side": side,
                "confirmed": confirmed,
                "confirmed_side": side if confirmed else None,
            }
        )
    return consensus(components)


def consensus(components: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Leading confirmed side. Selection requires all three lines."""
    counts: Dict[str, int] = {}
    for item in components:
        side = item.get("confirmed_side")
        if item.get("confirmed") and side:
            counts[str(side)] = counts.get(str(side), 0) + 1
    agreement_count = 0
    side: Optional[str] = None
    if counts:
        side, agreement_count = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
    selected = agreement_count == len(COMPONENT_ORDER) and len(components) >= len(COMPONENT_ORDER)
    if not selected:
        side = side if agreement_count else None
    return {
        "schema_version": "1.0.0",
        "label": DISPLAY_LABEL,
        "phase": "3/3" if selected else f"{agreement_count}/3",
        "display_phase": phase_text(agreement_count, selected),
        "selected": selected,
        "side": side,
        "agreement_count": agreement_count,
        "components": components,
    }
