"""Persistence. SQLite is sufficient at a few hundred rows a day; SQLAlchemy keeps
the move to Postgres a connection-string change.

Every run is recorded whole — candidates, scores, rationales, what was selected and
what was not — because the point of storing it is to be able to replay a stage with
a changed rubric and diff the result, not merely to render a page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from .config import ROOT
from .models import ScoredItem, Usage

DB_PATH = ROOT / "data" / "ai_radar.db"


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    model: Mapped[str] = mapped_column(String(120), default="")
    ingested: Mapped[int] = mapped_column(Integer, default=0)
    scored: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    stories: Mapped[list[Story]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Story.rank"
    )

    @property
    def precision(self) -> float | None:
        """Share of DELIVERED stories you kept, among those you voted on.

        The efficacy measure: a digest is working when most of what it sends is
        worth keeping. None until something is voted — an unvoted run has no score,
        which is different from scoring zero.
        """
        voted = [s for s in self.stories if s.selected and s.verdict != 0]
        if not voted:
            return None
        return sum(1 for s in voted if s.verdict == 1) / len(voted)


class Story(Base):
    """One scored candidate from one run, selected or not."""

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    canonical_url: Mapped[str] = mapped_column(String(1000), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str] = mapped_column(String(80))
    source_name: Mapped[str] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    cluster_id: Mapped[str] = mapped_column(String(60), index=True)
    total: Mapped[float] = mapped_column(Float, index=True)
    direct_relevance: Mapped[int] = mapped_column(Integer, default=0)
    actionability: Mapped[int] = mapped_column(Integer, default=0)
    novelty: Mapped[int] = mapped_column(Integer, default=0)
    significance: Mapped[int] = mapped_column(Integer, default=0)
    source_credibility: Mapped[int] = mapped_column(Integer, default=0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[Run] = relationship(back_populates="stories")
    votes: Mapped[list[Vote]] = relationship(back_populates="story", cascade="all, delete-orphan")

    @property
    def verdict(self) -> int:
        """Latest vote: 1 up, -1 down, 0 none."""
        return self.votes[-1].verdict if self.votes else 0


class Vote(Base):
    """A judgement from the reader. This is the training signal; nothing else is."""

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id"), index=True)
    verdict: Mapped[int] = mapped_column(Integer)  # +1 or -1
    note: Mapped[str] = mapped_column(Text, default="")
    surface: Mapped[str] = mapped_column(String(20), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    story: Mapped[Story] = relationship(back_populates="votes")


def engine_for(path: Path | None = None):  # type: ignore[no-untyped-def]
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(f"sqlite:///{target}", future=True)
    Base.metadata.create_all(eng)
    return eng


def record_run(
    session: Session,
    *,
    model: str,
    ingested: int,
    scored: list[ScoredItem],
    selected: list[ScoredItem],
    usage: Usage,
    duration_s: float,
) -> Run:
    """Persist a whole run. Selected stories keep their rank; the rest are kept too,
    because the rejections are what show a rubric going wrong."""
    chosen = {id(s) for s in selected}
    run = Run(
        model=model,
        ingested=ingested,
        scored=len(scored),
        selected=len(selected),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        duration_s=round(duration_s, 1),
    )
    order = {id(s): n for n, s in enumerate(selected, 1)}
    for s in scored:
        run.stories.append(
            Story(
                canonical_url=s.item.canonical_url,
                url=s.item.url,
                title=s.item.title,
                summary=s.item.excerpt(800),
                source_id=s.item.source_id,
                source_name=s.item.source_name,
                published_at=s.item.published_at.replace(tzinfo=None)
                if s.item.published_at
                else None,
                cluster_id=s.score.cluster_id,
                total=s.total,
                direct_relevance=s.score.direct_relevance,
                actionability=s.score.actionability,
                novelty=s.score.novelty,
                significance=s.score.significance,
                source_credibility=s.score.source_credibility,
                rationale=s.score.rationale,
                selected=id(s) in chosen,
                rank=order.get(id(s), 0),
            )
        )
    session.add(run)
    session.commit()
    return run


def already_seen(session: Session, urls: list[str], delivered_only: bool = False) -> set[str]:
    """Canonical URLs this system has already judged.

    Defaults to every story ever scored, not merely those delivered. Two reasons: a
    story rejected yesterday should not reappear tomorrow asking for the same
    verdict, and at 90 seconds a call, re-scoring yesterday's candidates is the
    largest avoidable cost in a run.
    """
    if not urls:
        return set()
    stmt = select(Story.canonical_url).where(Story.canonical_url.in_(urls))
    if delivered_only:
        stmt = stmt.where(Story.selected.is_(True))
    return set(session.scalars(stmt))
