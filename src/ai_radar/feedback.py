"""Turning votes into a calibration signal for the scorer.

This is the whole learning mechanism. There is no fine-tuning and no embedding
store: the reader's past verdicts are replayed into the scoring prompt as worked
examples, so the model calibrates against this reader rather than against a generic
notion of "interesting". It is cheap, inspectable, and reversible — you can read
exactly what the model was told, and deleting a vote undoes its influence.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Story, Vote


def _recent(session: Session, verdict: int, limit: int) -> list[tuple[str, str, str]]:
    """Most recent (title, cluster, note) for one verdict, newest first.

    Grouped by story so a story voted twice contributes once, using its latest vote.
    """
    latest = (
        select(Vote.story_id, func.max(Vote.id).label("vote_id")).group_by(Vote.story_id).subquery()
    )
    rows = session.execute(
        select(Story.title, Story.cluster_id, Vote.note)
        .join(latest, latest.c.story_id == Story.id)
        .join(Vote, Vote.id == latest.c.vote_id)
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
    up = session.scalar(select(func.count()).select_from(Vote).where(Vote.verdict == 1)) or 0
    down = session.scalar(select(func.count()).select_from(Vote).where(Vote.verdict == -1)) or 0
    return up, down
