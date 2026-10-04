from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_radar.config import load_profile, load_sources
from ai_radar.ingest import canonicalise, dedupe
from ai_radar.models import RawItem, Score, ScoredItem
from ai_radar.render import render_digest
from ai_radar.score import build_system_prompt
from ai_radar.select import select


def item(title: str, url: str, days_old: int = 0) -> RawItem:
    return RawItem(
        source_id="s",
        source_name="Source",
        title=title,
        url=url,
        canonical_url=canonicalise(url),
        summary="body",
        published_at=datetime.now(UTC) - timedelta(days=days_old),
    )


def scored(title: str, cluster: str, total: float) -> ScoredItem:
    score = Score(
        cluster_id=cluster,
        direct_relevance=int(total),
        actionability=0,
        novelty=0,
        significance=0,
        source_credibility=0,
        rationale="because",
    )
    return ScoredItem(item=item(title, f"https://e.com/{title}"), score=score, total=total)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://E.com/post/?utm_source=x", "https://e.com/post"),
        ("https://e.com/post#section", "https://e.com/post"),
        ("https://e.com/post?id=7&utm_medium=rss", "https://e.com/post?id=7"),
    ],
)
def test_canonicalise_strips_tracking(raw: str, expected: str) -> None:
    assert canonicalise(raw) == expected


def test_dedupe_collapses_tracking_variants_and_drops_stale() -> None:
    items = [
        item("A", "https://e.com/a"),
        item("A again", "https://e.com/a?utm_source=rss"),
        item("Old", "https://e.com/old", days_old=30),
    ]
    kept = dedupe(items, max_age_days=7)
    assert [i.title for i in kept] == ["A"]


def test_dedupe_keeps_undated_items() -> None:
    undated = item("No date", "https://e.com/n")
    undated.published_at = None
    assert dedupe([undated], max_age_days=7) == [undated]


def test_select_enforces_diversity_cap() -> None:
    candidates = [scored(f"tts{i}", "tts", 90 - i) for i in range(5)]
    candidates += [scored("stt0", "stt", 50), scored("india0", "india", 40)]
    chosen, _ = select(candidates, count=3, max_per_cluster=2)
    clusters = [c.cluster_id for c in chosen]
    assert clusters.count("tts") == 2
    assert len(chosen) == 3


def test_select_backfills_rather_than_underfilling() -> None:
    candidates = [scored(f"tts{i}", "tts", 90 - i) for i in range(5)]
    chosen, _ = select(candidates, count=4, max_per_cluster=2)
    assert len(chosen) == 4, "cap must not starve the digest below the requested count"


def test_weighted_total_uses_rubric_not_the_model() -> None:
    profile = load_profile()
    score = Score(
        cluster_id="tts",
        direct_relevance=100,
        actionability=0,
        novelty=0,
        significance=0,
        source_credibility=0,
        rationale="r",
    )
    assert score.weighted_total(profile.rubric) == pytest.approx(35.0)


def test_system_prompt_names_no_vendors() -> None:
    """The ranker must match capabilities, not a watchlist of companies."""
    prompt = build_system_prompt(load_profile()).lower()
    for vendor in ("deepgram", "elevenlabs", "vapi", "retell", "cartesia", "sarvam"):
        assert vendor not in prompt


def test_render_digest_includes_links_and_scores() -> None:
    profile = load_profile()
    out = render_digest([scored("Headline", "tts", 77.0)], profile, datetime.now(UTC))
    assert "Headline" in out
    assert "score 77.0" in out
    assert "https://e.com/Headline" in out


def test_configs_load_and_weights_are_sane() -> None:
    profile = load_profile()
    assert sum(profile.rubric.values()) == pytest.approx(1.0)
    assert sum(c.weight for c in profile.clusters) == pytest.approx(1.0)
    assert load_sources().enabled


def test_interleave_represents_every_source_at_any_cutoff() -> None:
    """A flat concatenation would let the largest source monopolise the candidate pool."""
    from ai_radar.ingest import interleave

    big = [item(f"arxiv{i}", f"https://arxiv.org/{i}") for i in range(100)]
    small = [item("news0", "https://news.com/0"), item("news1", "https://news.com/1")]
    merged = interleave([big, small])
    assert merged[1].title == "news0", "second slot must come from the smaller source"
    assert {i.source_id for i in merged[:4]} and "news1" in {i.title for i in merged[:4]}


def test_no_source_is_configured_to_be_empty_on_some_days() -> None:
    """The digest runs all seven days, so no source may be empty by design."""
    import yaml

    from ai_radar.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text())
    for source in raw["sources"]:
        assert "weekdays_only" not in source, f"{source['id']} declares a skip day"
        assert "rss.arxiv.org" not in source["url"], (
            f"{source['id']} uses arXiv RSS, which is empty at weekends; use the API"
        )
