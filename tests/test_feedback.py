"""The feedback loop is the only thing that makes the ranker personal, so its
mechanics are pinned here: votes persist, the latest one wins, and they reach the
prompt."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from ai_radar.config import load_profile
from ai_radar.db import Run, Story, Vote, engine_for
from ai_radar.feedback import learned_examples, vote_counts
from ai_radar.score import build_system_prompt


@pytest.fixture
def session(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = engine_for(tmp_path / "t.db")
    with Session(engine) as s:
        run = Run(model="test")
        run.stories = [
            Story(
                canonical_url="https://e/1",
                url="https://e/1",
                title="Kept story",
                source_id="s",
                source_name="S",
                cluster_id="tts",
                total=70.0,
                selected=True,
            ),
            Story(
                canonical_url="https://e/2",
                url="https://e/2",
                title="Rejected story",
                source_id="s",
                source_name="S",
                cluster_id="stt",
                total=65.0,
                selected=True,
            ),
        ]
        s.add(run)
        s.commit()
        yield s


def test_no_votes_means_no_prompt_block(session: Session) -> None:
    """An unvoted system must score on the rubric alone, not invented preferences."""
    assert learned_examples(session) == ""


def test_votes_reach_the_prompt_with_their_verdict(session: Session) -> None:
    kept, rejected = session.query(Story).order_by(Story.id).all()
    session.add_all(
        [
            Vote(story_id=kept.id, verdict=1),
            Vote(story_id=rejected.id, verdict=-1, note="too academic"),
        ]
    )
    session.commit()

    block = learned_examples(session)
    assert "Kept story" in block
    assert "Rejected story" in block
    assert "too academic" in block, "the reader's note is the most specific signal"

    kept_at = block.index("Kept story")
    rejected_at = block.index("Rejected story")
    assert block.index("They KEPT") < kept_at < block.index("They REJECTED") < rejected_at

    prompt = build_system_prompt(load_profile(), block)
    assert "Kept story" in prompt and "Rejected story" in prompt


def test_latest_vote_wins_so_a_change_of_mind_is_not_double_counted(
    session: Session,
) -> None:
    story = session.query(Story).first()
    assert story
    session.add(Vote(story_id=story.id, verdict=1))
    session.commit()
    session.add(Vote(story_id=story.id, verdict=-1))
    session.commit()

    block = learned_examples(session)
    assert block.count(story.title) == 1, "a story must appear once, under its latest verdict"
    assert block.index("They REJECTED") < block.index(story.title)


def test_vote_counts(session: Session) -> None:
    a, b = session.query(Story).order_by(Story.id).all()
    session.add_all([Vote(story_id=a.id, verdict=1), Vote(story_id=b.id, verdict=-1)])
    session.commit()
    assert vote_counts(session) == (1, 1)


def test_already_seen_covers_rejected_stories_not_only_delivered(
    session: Session,
) -> None:
    """A story rejected yesterday must not come back asking for the same verdict,
    and must not cost another 90-second scoring call."""
    from ai_radar.db import Run, Story, already_seen

    run = Run(model="t")
    run.stories = [
        Story(
            canonical_url="https://e/kept",
            url="https://e/kept",
            title="k",
            source_id="s",
            source_name="S",
            cluster_id="tts",
            total=80.0,
            selected=True,
        ),
        Story(
            canonical_url="https://e/dropped",
            url="https://e/dropped",
            title="d",
            source_id="s",
            source_name="S",
            cluster_id="tts",
            total=4.0,
            selected=False,
        ),
    ]
    session.add(run)
    session.commit()

    urls = ["https://e/kept", "https://e/dropped", "https://e/brand-new"]
    assert already_seen(session, urls) == {"https://e/kept", "https://e/dropped"}
    assert already_seen(session, urls, delivered_only=True) == {"https://e/kept"}
    assert "https://e/brand-new" not in already_seen(session, urls)


def test_one_article_is_one_example_however_often_it_was_served(
    session: Session,
) -> None:
    """Before cross-run dedupe existed, the same article was re-served daily and
    voted down each time. Counting story rows let one article fill the prompt and
    drown out every other signal."""
    from ai_radar.db import Run, Story

    for _ in range(4):
        run = Run(model="t")
        run.stories = [
            Story(
                canonical_url="https://e/repeat",
                url="https://e/repeat",
                title="Served every morning",
                source_id="s",
                source_name="S",
                cluster_id="stt",
                total=50.0,
                selected=True,
            )
        ]
        session.add(run)
    session.commit()

    for story in session.query(Story).filter(Story.canonical_url == "https://e/repeat"):
        session.add(Vote(story_id=story.id, verdict=-1))
    session.commit()

    block = learned_examples(session)
    assert block.count("Served every morning") == 1, "one article, one example"
    assert vote_counts(session) == (0, 1), "four rows for one article is one verdict"
