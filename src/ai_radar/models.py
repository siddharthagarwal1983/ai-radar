"""Data shapes passed between pipeline stages.

Every stage is a pure function from a list of these to a list of these, which is
what makes a stage replayable from stored input in later phases.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

SUBSCORES = (
    "direct_relevance",
    "actionability",
    "novelty",
    "significance",
    "source_credibility",
)


class RawItem(BaseModel):
    """One story as it came off a feed, normalised."""

    source_id: str
    source_name: str
    title: str
    url: str
    canonical_url: str
    summary: str = ""
    published_at: datetime | None = None

    def excerpt(self, limit: int = 1200) -> str:
        text = self.summary.strip()
        return text[:limit] + ("…" if len(text) > limit else "")


class Score(BaseModel):
    """The judgement for one item. Subscores come from the model; `total` does not.

    Weighting is applied by us, from the rubric in profile.yaml, so the weights can
    be retuned without touching the prompt or re-running the model.
    """

    cluster_id: str
    direct_relevance: int = Field(ge=0, le=100)
    actionability: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    significance: int = Field(ge=0, le=100)
    source_credibility: int = Field(ge=0, le=100)
    rationale: str
    always_surface: bool = False
    never_surface: bool = False

    def weighted_total(self, rubric: dict[str, float]) -> float:
        total: float = sum(float(getattr(self, k)) * rubric.get(k, 0.0) for k in SUBSCORES)
        return round(total, 1)


class ScoredItem(BaseModel):
    item: RawItem
    score: Score
    total: float

    @property
    def cluster_id(self) -> str:
        return self.score.cluster_id


class Usage(BaseModel):
    """Per-call spend, kept per model so the dashboard can break cost down later."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            model=self.model if self.model == other.model else "mixed",
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 6),
        )
