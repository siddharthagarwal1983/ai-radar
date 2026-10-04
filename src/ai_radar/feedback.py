"""Turning votes into a calibration signal for the scorer.

This is the whole learning mechanism. There is no fine-tuning and no embedding
store: the reader's past verdicts are replayed into the scoring prompt as worked
examples, so the model calibrates against this reader rather than against a generic
notion of "interesting". It is cheap, inspectable, and reversible — you can read
exactly what the model was told, and deleting a vote undoes its influence.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Story, Vote


def _latest_per_article(session: Session) -> Any:
    """Newest vote id for each distinct ARTICLE, not each story row.

    A story re-served across runs gets a Story row per run, so voting it down on
    three mornings produced three rows. Grouping by story id let the same article
    appear repeatedly in the prompt and drown out everything else; grouping by
    canonical URL means one article is one example, under its most recent verdict.
    """
    return (
        select(
            Story.canonical_url.label("url"),
            func.max(Vote.id).label("vote_id"),
        )
        .join(Vote, Vote.story_id == Story.id)
        .group_by(Story.canonical_url)
        .subquery()
    )


def _recent(session: Session, verdict: int, limit: int) -> list[tuple[str, str, str]]:
    """Most recent (title, cluster, note) for one verdict, newest first, one per
    article."""
    latest = _latest_per_article(session)
    rows = session.execute(
        select(Story.title, Story.cluster_id, Vote.note)
        .select_from(latest)
        .join(Vote, Vote.id == latest.c.vote_id)
        .join(Story, Story.id == Vote.story_id)
        .where(Vote.verdict == verdict)
        .order_by(Vote.id.desc())
        .limit(limit)
    ).all()
    return [(t, c, n or "") for t, c, n in rows]


def learned_examples(session: Session, per_side: int = 12) -> str:
    """A prompt block of what this reader has approved and rejected.

    Returns an empty string until votes exist, so an unvoted system scores on the
    rubric alone rather than on invented preferences.
    """
    up = _recent(session, 1, per_side)
    down = _recent(session, -1, per_side)
    if not up and not down:
        return ""

    def fmt(rows: list[tuple[str, str, str]]) -> str:
        out = []
        for title, cluster, note in rows:
            line = f"- [{cluster}] {title}"
            if note:
                line += f"  (reader's note: {note})"
            out.append(line)
        return "\n".join(out)

    block = [
        "",
        "THIS READER'S OWN VERDICTS on earlier digests. These outrank your general "
        "sense of what is interesting: match the pattern they show, including where "
        "it contradicts the weights above.",
    ]
    if up:
        block += ["", "They KEPT these — score stories like them high:", fmt(up)]
    if down:
        block += [
            "",
            "They REJECTED these — score stories like them low, even when the topic looks on-beat:",
            fmt(down),
        ]
    return "\n".join(block)


def vote_counts(session: Session) -> tuple[int, int]:
    """Current verdicts per article — not raw vote rows.

    Counting rows would report a superseded vote, and an article judged on three
    mornings, as four separate opinions.
    """
    latest = _latest_per_article(session)
    rows = (
        session.execute(
            select(Vote.verdict).select_from(latest).join(Vote, Vote.id == latest.c.vote_id)
        )
        .scalars()
        .all()
    )
    return sum(1 for v in rows if v == 1), sum(1 for v in rows if v == -1)
