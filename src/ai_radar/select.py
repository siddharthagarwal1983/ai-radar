"""Stage 6: pick the digest, with the diversity cap applied.

Without the cap, a busy week in one cluster takes every slot — and that is exactly
the week the other clusters are worth seeing.
"""

from __future__ import annotations

from collections import Counter

from .models import ScoredItem


def select(
    scored: list[ScoredItem], count: int, max_per_cluster: int, floor: float = 1.0
) -> tuple[list[ScoredItem], list[ScoredItem]]:
    """Return (selected, rejected). Rejected keeps its reason via score.rationale."""
    ranked = sorted(scored, key=lambda s: s.total, reverse=True)
    chosen: list[ScoredItem] = []
    per_cluster: Counter[str] = Counter()
    overflow: list[ScoredItem] = []

    for candidate in ranked:
        if len(chosen) >= count:
            break
        if candidate.total < floor:
            continue
        if per_cluster[candidate.cluster_id] >= max_per_cluster:
            overflow.append(candidate)
            continue
        chosen.append(candidate)
        per_cluster[candidate.cluster_id] += 1

    # If the cap starved the digest, backfill from what it displaced rather than
    # delivering three stories when five were asked for.
    if len(chosen) < count:
        for candidate in overflow:
            if len(chosen) >= count:
                break
            chosen.append(candidate)

    picked = {id(c) for c in chosen}
    rejected = [s for s in ranked if id(s) not in picked]
    return chosen, rejected
