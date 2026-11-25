import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from src.core.config import Settings
from src.core.models import JobPosting, Precision

BR = ZoneInfo("America/Sao_Paulo")
log = logging.getLogger("coleta")


def content(value: str | None) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def parse_time(value: str | None) -> tuple[datetime | None, Precision]:
    if not value:
        return None, "unknown"
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(), BR), "day"
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo:
            return stamp, "timestamp"
    except ValueError:
        pass
    return None, "unknown"


@dataclass
class Context:
    settings: Settings
    now: datetime
    hours: int
    last_nube: datetime | None
    known_nube: set[str]
    client: httpx.AsyncClient

    @property
    def cutoff(self) -> datetime:
        return self.now - timedelta(hours=self.hours)


class BaseCollector(ABC):
    def __init__(self, name: str, ctx: Context):
        self.name = name
        self.ctx = ctx
        self.errors = 0
        self.successes = 0

    @abstractmethod
    def queries(self) -> Iterable[str]:
        """Cada consulta falha de forma independente."""

    @abstractmethod
    def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        """Produz as vagas de uma consulta pública."""

    async def collect(self) -> AsyncIterator[JobPosting]:
        for query in self.queries():
            try:
                async for job in self.fetch(query):
                    yield job
                self.successes += 1
            except Exception as error:
                self.errors += 1
                log.warning("%s · %s · %s", self.name, query, safe_error(error))

    async def get(self, url: str, **options: Any) -> httpx.Response:
        for attempt in range(3):
            await asyncio.sleep(0.35 if attempt == 0 else 2 ** attempt)
            try:
                response = await self.ctx.client.get(url, **options)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                continue
            # Um bloqueio ou limite de uso adia a fonte para a rodada seguinte.
            if response.status_code not in (502, 503, 504) or attempt == 2:
                response.raise_for_status()
                return response
        raise RuntimeError("Consulta sem resposta")


def safe_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, (RuntimeError, TimeoutError)):
        return str(error)[:180] or type(error).__name__
    return type(error).__name__
