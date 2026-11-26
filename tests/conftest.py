from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.config import Settings
from src.core.models import JobPosting


@pytest.fixture
def settings():
    return Settings.read(Path(__file__).resolve().parents[1] / "config.yaml")


@pytest.fixture
def now():
    return datetime(2026, 11, 25, 16, tzinfo=timezone.utc)


@pytest.fixture
def job(now):
    return JobPosting(title="Desenvolvedor Mobile Júnior", company="Ação Digital",
                      url="https://empresa.test/jobs/123?utm_source=vagas", source="gupy",
                      location="Curitiba, Brasil", is_remote=True, published_at=now, precision="timestamp",
                      description="Construir APIs com Python, Git e SQL.")
