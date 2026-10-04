"""Stage 8: one canonical Markdown digest. Every sink renders from this."""

from __future__ import annotations

from datetime import datetime

from .config import Profile
from .models import ScoredItem, Usage


def render_digest(
    selected: list[ScoredItem],
    profile: Profile,
    when: datetime,
    usage: Usage | None = None,
) -> str:
    clusters = profile.cluster_by_id
    lines = [
        f"# AI Radar — {when.strftime('%A %d %B %Y')}",
        "",
        f"{len(selected)} stories, ranked against your profile.",
        "",
    ]

    for n, s in enumerate(selected, 1):
        cluster = clusters[s.cluster_id].name if s.cluster_id in clusters else "Unclassified"
        date = s.item.published_at.date().isoformat() if s.item.published_at else "undated"
        lines += [
            f"## {n}. {s.item.title}",
            "",
            f"**{cluster}** · score {s.total} · {s.item.source_name} · {date}",
            "",
            f"{s.score.rationale}",
            "",
            f"[Read it]({s.item.url})",
            "",
        ]

    if usage and usage.cost_usd:
        lines += [
            "---",
            "",
            (
                f"_{usage.model}: {usage.input_tokens:,} in / {usage.output_tokens:,} out "
                f"· ${usage.cost_usd:.4f}_"
            ),
        ]

    return "\n".join(lines)


def render_funnel(counts: dict[str, int]) -> str:
    return " → ".join(f"{k} {v}" for k, v in counts.items())
