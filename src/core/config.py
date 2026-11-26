from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Source = Literal["gupy", "github", "linkedin", "indeed", "infojobs", "vagascom", "nube", "programathor",
                 "amazon", "greenhouse", "ashby", "himalayas", "comunidades", "web", "hackernews", "weworkremotely"]
Repo = Annotated[str, StringConstraints(pattern=r"^[\w.-]+/[\w.-]+$")]
Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]+$")]


class AiBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finalists_per_run: int = Field(default=3, ge=0, le=100)
    requests_per_24h: int = Field(default=6, ge=0)
    tokens_per_24h: int = Field(default=24_000, ge=0)
    tokens_per_minute: int = Field(default=12_000, ge=1)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_terms: list[Term] = Field(min_length=1)
    roles: list[Term] = Field(min_length=1)
    stack: list[Term]
    positive: list[Term] = Field(min_length=1)
    blocked: list[Term]
    excluded_areas: list[Term] = Field(default_factory=list)
    hours: int = Field(ge=1, le=720)
    min_score: float = Field(ge=0, le=10)
    ai_budget: AiBudget = Field(default_factory=AiBudget)
    remote: bool
    cities: list[Term]
    sources: list[Source] = Field(min_length=1)
    github_repos: list[Repo]
    greenhouse_boards: dict[Slug, Term] = Field(default_factory=dict)
    ashby_boards: dict[Slug, Term] = Field(default_factory=dict)
    community_channels: list[Slug] = Field(default_factory=list)
    web_queries: list[Term] = Field(default_factory=list, max_length=30)
    web_pages_per_query: int = Field(default=5, ge=1, le=20)
    forms_lookback_days: int = Field(default=30, ge=1, le=90)
    infojobs_cities: list[Term]
    results_per_search: int = Field(ge=1, le=200)
    database: Path
    resume: Path

    @classmethod
    def read(cls, path: Path) -> "Settings":
        settings = cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")))
        settings.database = path.parent / settings.database
        settings.resume = path.parent / settings.resume
        return settings
