"""Configuration loading: profile.yaml, sources.yaml, and provider keys from env."""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"

# Application Default Credentials, as written by `gcloud auth application-default login`.
ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


class Cluster(BaseModel):
    id: str
    name: str
    weight: float
    counts: str


class Source(BaseModel):
    id: str
    name: str
    url: str
    enabled: bool = True


class VertexConfig(BaseModel):
    """Vertex AI needs a project and a location; ADC supplies neither for a user
    credential, so the project is read from the ADC file unless overridden here."""

    project: str | None = None
    location: str = "global"


class Profile(BaseModel):
    identity: dict[str, str]
    digest: dict[str, Any]
    models: dict[str, str]
    rubric: dict[str, float]
    clusters: list[Cluster]
    always_surface: str
    never_surface: str
    vertex: VertexConfig = VertexConfig()
    limits: dict[str, Any] = {}

    @cached_property
    def cluster_by_id(self) -> dict[str, Cluster]:
        return {c.id: c for c in self.clusters}

    @property
    def story_count(self) -> int:
        return int(self.digest.get("story_count", 5))

    @property
    def max_per_cluster(self) -> int:
        return int(self.digest.get("max_per_cluster", 2))

    @property
    def request_spacing(self) -> float:
        return float(self.limits.get("request_spacing_seconds", 1.5))


class SourceSet(BaseModel):
    defaults: dict[str, Any]
    sources: list[Source]
    no_feed_found: list[str] = []

    @property
    def enabled(self) -> list[Source]:
        return [s for s in self.sources if s.enabled]

    @property
    def max_items_per_source(self) -> int:
        return int(self.defaults.get("max_items_per_source", 40))

    @property
    def max_age_days(self) -> int:
        return int(self.defaults.get("max_age_days", 7))


class Settings(BaseSettings):
    """Provider keys. LiteLLM reads these from the environment directly."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    # Speech API (STT/TTS), not an LLM provider. Never routed to by the model layer.
    deepgram_api_key: str | None = None

    def key_for(self, model: str) -> str | None:
        """What credential backs this model, if any. Vertex uses ADC, not a key."""
        provider = model.split("/", 1)[0] if "/" in model else "anthropic"
        if provider == "vertex_ai":
            return "adc" if ADC_PATH.exists() else None
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
        }.get(provider)


def load_profile(path: Path | None = None) -> Profile:
    data = yaml.safe_load((path or CONFIG_DIR / "profile.yaml").read_text())
    return Profile.model_validate(data)


def load_sources(path: Path | None = None) -> SourceSet:
    data = yaml.safe_load((path or CONFIG_DIR / "sources.yaml").read_text())
    return SourceSet.model_validate(data)


def adc_quota_project() -> str | None:
    """The project id recorded in the ADC file, used when nothing overrides it."""
    if not ADC_PATH.exists():
        return None
    try:
        return str(json.loads(ADC_PATH.read_text()).get("quota_project_id") or "") or None
    except (OSError, json.JSONDecodeError):
        return None


def vertex_kwargs(profile: Profile) -> dict[str, str]:
    """Extra arguments LiteLLM needs for a vertex_ai/* model."""
    project = profile.vertex.project or adc_quota_project()
    if not project:
        return {}
    return {"vertex_project": project, "vertex_location": profile.vertex.location}
