import asyncio
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import StringIO

import httpx
import pytest
from rich.console import Console

from src.collectors.base import BaseCollector
from src.core.hunt import hunt
from src.storage.repository import Repository


def test_local_hunt_deduplicates_and_ignores_legacy_bot(monkeypatch, settings, job, tmp_path):
    class Board(BaseCollector):
        def queries(self):
            return ["termo"]

        async def fetch(self, query):
            if self.name == "github":
                raise RuntimeError("Fonte temporariamente indisponível")
            yield job.model_copy(update={"source":self.name, "published_at":datetime.now(timezone.utc),
                                         "url":"https://example.com/"+self.name})

    from src.core import hunt as module
    monkeypatch.setattr(module,"COLLECTORS",dict.fromkeys(["github","gupy","indeed"],Board))
    def unexpected_request(request):
        pytest.fail("Uma coleta local simulada tentou fazer um envio HTTP")
    client_type = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(unexpected_request), **kw))
    monkeypatch.setenv("TELEGRAM_TOKEN","teste")
    monkeypatch.setenv("TELEGRAM_CHAT_ID","123")
    settings.sources=["github","gupy","indeed"]
    settings.database=tmp_path/"jobs.db"
    console=Console(file=StringIO())
    def run(preview):
        repo=Repository(settings.database,preview=preview)
        with closing(repo.db):
            return asyncio.run(hunt(settings,repo,2,preview,True,console))

    assert run(True)==0 and not settings.database.exists()
    assert run(False)==0
    assert run(False)==0
    repo = Repository(settings.database)
    with closing(repo.db):
        assert len(list(repo.approved())) == 1
        assert repo.last_hunt()["sources_ok"] == 2 and repo.last_hunt()["errors"] == 1
    settings.sources=["github"]
    assert run(True)==1


def test_remote_hunt_filters_before_ai_and_keeps_jobs_when_quota_ends(monkeypatch, settings, job, tmp_path):
    from src.core import hunt as module

    class Board(BaseCollector):
        def queries(self):
            return ["dev"]

        async def fetch(self, query):
            for index, title in enumerate(["Desenvolvedor Sênior", "Desenvolvedor Mobile Júnior", "Desenvolvedor Web Júnior"]):
                yield job.model_copy(update={"title": title, "url": f"https://example.test/{index}",
                                             "description": "Requisitos obrigatórios para desenvolver APIs com Python, Git e SQL.",
                                             "published_at": datetime.now(timezone.utc)})

    calls = []

    def respond(request):
        assert request.url.path == "/v1beta/decisions"
        body = json.loads(request.content)
        calls.append(json.loads(body["input"])["titulo"])
        answers = {"fit": {"type": "score", "score": 3.2}, "risk": {"type": "choice", "choice": "nenhum"},
                   "level": {"type": "choice", "choice": "junior"}}
        return httpx.Response(200, json={"answers": answers, "usage": {"total_tokens": 600}})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(module, "COLLECTORS", {"gupy": Board})
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_MODEL", "gemini-decision-1")
    monkeypatch.setenv("GEMINI_API_KEY", "teste")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    settings.sources = ["gupy"]
    settings.resume = tmp_path / "perfil.md"
    settings.resume.write_text("Experiência com Python, Git e SQL.", encoding="utf-8")
    settings.ai_budget.requests_per_24h = 1
    path = tmp_path / "vagas.sqlite"

    def run(preview):
        repo = Repository(path, preview=preview)
        with closing(repo.db):
            assert asyncio.run(hunt(settings, repo, 2, preview, False, Console(file=StringIO()))) == 0
            return [json.loads(row["decision"]) for row in repo.latest() if row["delivery"] == "pending"]

    assert len(run(True)) == 2 and not calls and not path.exists()
    assert len(run(False)) == 2
    assert len(run(True)) == 2
    results = run(False)
    assert calls == ["Desenvolvedor Mobile Júnior"]
    assert sum(d["analysis"] is not None for d in results) == 1
    assert sum(d["ai_unavailable"] for d in results) == 1


@pytest.mark.parametrize(("hours", "extend", "approved"), [(24, False, 1), (168, False, 2), (24, True, 2)])
def test_user_window_applies_to_form_queries_checks_and_results(hours, extend, approved, monkeypatch, settings, job, tmp_path):
    from src.collectors.google_forms import FormInspection
    from src.core import hunt as runner

    calls = []
    class Board(BaseCollector):
        def queries(self):
            return ["forms"]

        async def fetch(self, query):
            assert self.ctx.form_hours == (settings.forms_lookback_days * 24 if extend else hours)
            for age in (1, 120):
                yield job.model_copy(update={"title": f"Dev Jr {age}h", "url": f"https://empresa.test/{age}",
                                             "published_at": self.ctx.now - timedelta(hours=age),
                                             "form_url": f"https://forms.gle/F{age}"})

    async def read(client, url, roles):
        calls.append(url)
        return FormInspection(url, "open")

    settings.sources = ["gupy"]
    monkeypatch.setattr(runner, "COLLECTORS", {"gupy": Board})
    monkeypatch.setattr(runner, "read_form", read)
    with closing((repo := Repository(tmp_path / "periodo.db")).db):
        assert asyncio.run(hunt(settings, repo, hours, False, True, Console(file=StringIO()), extend_forms=extend)) == 0
        assert len(list(repo.visible_approved())) == len(calls) == approved
        assert repo.last_hunt()["hours"] == hours
