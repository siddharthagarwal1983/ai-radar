"""Stage 5: judge each candidate against the profile.

The prompt describes capabilities, never companies. A release from an unknown lab
must score the same as an incumbent's announcement; naming vendors here would
quietly turn the ranker into a watchlist.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from .config import Profile
from .llm import LLMError, LLMProvider
from .models import RawItem, Score, ScoredItem, Usage

SYSTEM = """You rank technology news for one specific reader. You are strict: most \
stories are not relevant to them, and saying so is the useful answer.

THE READER
{role}
{context}

CLUSTERS — judge what a story DOES, not who shipped it. A first release from an \
unknown lab scores exactly as high as the same capability from a large vendor. A \
familiar vendor's post that adds no capability scores low.
{clusters}

ALWAYS SURFACE (set always_surface true): {always}

NEVER SURFACE (set never_surface true): {never}

Score each dimension 0-100:
- direct_relevance: does it touch the reader's stack or decisions?
- actionability: could they do something with it this week?
- novelty: is the CAPABILITY new, not merely the company name?
- significance: does it shift the landscape, or is it incremental?
- source_credibility: primary source or reproducible claim, vs. churn. Cap this at \
70 for a vendor's own announcement so a familiar name cannot outrank a better \
result from an unfamiliar one.

Pick the single best-fitting cluster id. If nothing fits, use "none" and score low.
Write one sentence of rationale naming the specific capability, or its absence.

Reply with JSON only: {{"cluster_id": str, "direct_relevance": int, \
"actionability": int, "novelty": int, "significance": int, \
"source_credibility": int, "rationale": str, "always_surface": bool, \
"never_surface": bool}}"""


def build_system_prompt(profile: Profile, learned: str = "") -> str:
    """The rubric, plus whatever this reader's votes have taught us so far."""
    clusters = "\n".join(
        f"- {c.id} ({c.name}, weight {c.weight}): {c.counts}" for c in profile.clusters
    )
    return (
        SYSTEM.format(
            role=profile.identity.get("role", "").strip(),
            context=profile.identity.get("context", "").strip(),
            clusters=clusters,
            always=profile.always_surface.strip(),
            never=profile.never_surface.strip(),
        )
        + learned
    )


def _user_prompt(item: RawItem) -> str:
    date = item.published_at.date().isoformat() if item.published_at else "undated"
    return (
        f"Source: {item.source_name}\nPublished: {date}\n"
        f"Title: {item.title}\n\nExcerpt:\n{item.excerpt()}"
    )


def score_items(
    items: list[RawItem],
    profile: Profile,
    model: str,
    extra: Mapping[str, Any] | None = None,
    spacing: float | None = None,
    learned: str = "",
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[ScoredItem], Usage, list[str]]:
    """Score one item at a time, pausing between calls.

    Serial and paced rather than parallel: this is a once-a-day batch with no
    deadline, so there is nothing to buy with concurrency, and bursts are what
    trip rate limits. A failed item is reported and skipped — one bad story must
    not cost the whole morning's digest.
    """
    provider = LLMProvider(model, extra=extra)
    system = build_system_prompt(profile, learned)
    gap = profile.request_spacing if spacing is None else spacing

    scored: list[ScoredItem] = []
    spend = Usage(model=model)
    errors: list[str] = []

    for index, item in enumerate(items):
        if index:
            time.sleep(gap)
        try:
            score, usage = provider.structured(system, _user_prompt(item), Score, max_tokens=1500)
        except LLMError as exc:
            errors.append(f"{item.title[:60]}: {exc}")
            continue
        spend = spend + usage
        total = 0.0 if score.never_surface else score.weighted_total(profile.rubric)
        if score.always_surface:
            total = max(total, 85.0)
        scored.append(ScoredItem(item=item, score=score, total=total))
        if on_progress:
            on_progress(index + 1, len(items))

    scored.sort(key=lambda s: s.total, reverse=True)
    return scored, spend, errors


# --- No-LLM mode ------------------------------------------------------------
# A keyword stand-in so the pipeline is runnable without a provider key. It is a
# placeholder for the embedding prefilter in Phase 1, not a ranker: it matches
# words, so it cannot tell a new capability from a press release that mentions one.

_WORD = re.compile(r"[a-z][a-z0-9+-]{2,}")


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def score_items_heuristic(items: list[RawItem], profile: Profile) -> list[ScoredItem]:
    cluster_terms = {c.id: _terms(f"{c.name} {c.counts}") for c in profile.clusters}
    weights = {c.id: c.weight for c in profile.clusters}
    never = _terms(profile.never_surface)

    scored: list[ScoredItem] = []
    for item in items:
        words = _terms(f"{item.title} {item.excerpt(600)}")
        best_id, best_overlap = "none", 0
        for cid, terms in cluster_terms.items():
            overlap = len(words & terms)
            if overlap > best_overlap:
                best_id, best_overlap = cid, overlap

        relevance = min(100, best_overlap * 9)
        penalty = len(words & never) * 4
        base = max(0, relevance - penalty)
        boosted = min(100, int(base * (1 + weights.get(best_id, 0.0))))
        score = Score(
            cluster_id=best_id,
            direct_relevance=boosted,
            actionability=base,
            novelty=base,
            significance=base,
            source_credibility=50,
            rationale=f"keyword overlap {best_overlap} with {best_id} (no-LLM mode)",
        )
        scored.append(
            ScoredItem(item=item, score=score, total=score.weighted_total(profile.rubric))
        )

    scored.sort(key=lambda s: s.total, reverse=True)
    return scored
