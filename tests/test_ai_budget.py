import asyncio
import json
from contextlib import closing

import httpx
import pytest

from src.core.config import AiBudget
from src.core.models import Decision, Evidence
from src.filters import decision_api
from src.filters.ai import assess, compact_input
from src.storage import repository
from src.storage.repository import Repository


@pytest.fixture
def decision(monkeypatch):
    monkeypatch.setenv("AI_MODEL", decision_api.MODEL)
    monkeypatch.setenv("GEMINI_API_KEY", "chave-de-teste")
    return Decision(eligible=True, score=7, skills=["SQL"], reason="regras")


def answer(request):
    body = json.loads(request.content)
    assert request.url.path == "/v1beta/decisions" and request.headers["x-goog-api-key"] == "chave-de-teste"
    assert body["model"] == decision_api.MODEL
    assert body["questions"]["fit"]["criteria"] == decision_api.RUBRIC
    answers = {"fit": {"type": "score", "score": 3.2}, "level": {"type": "choice", "choice": "junior"},
               "risk": {"type": "choice", "choice": "tecnica"}}
    for name in body["questions"]:
        if name.startswith("gap_"):
            answers[name] = {"type": "choice", "choice": "sim"}
    return httpx.Response(200, json={"answers": answers, "usage": {"total_tokens": 517, "total_output_tokens": 0}})


def test_persistent_cache_and_daily_limit(tmp_path, job, decision):
    path = tmp_path / "jobs.db"
    requests = []
    limits = AiBudget(requests_per_24h=1)
    job.description = "SQL e Kubernetes obrigatórios."

    def respond(request):
        requests.append(request)
        return answer(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            first = Repository(path)
            with closing(first.db):
                async def review(repo, profile="Curso de SQL", **options):
                    return await assess(client, "gemini", profile, job, decision, repo=repo, limits=limits, **options)
                assert (await review(first, preview=True)).ai_unavailable
                assert tuple(first.ai_usage()) == (0, 0)
                scored = await review(first)
                assert scored.score == decision.score and scored.analysis.score == 8
                assert scored.analysis.skills_faltantes == ["Kubernetes"]
                assert scored.analysis.red_flags == [decision_api.FLAGS["tecnica"]]
            second = Repository(path)
            with closing(second.db):
                assert (await review(second)).analysis == scored.analysis
                assert (await review(second, preview=True)).analysis == scored.analysis
                assert (await review(second, "Experiência em Java")).ai_unavailable
                assert tuple(second.ai_usage()) == (1, 517)
                assert len(requests) == 1
    asyncio.run(run())


def test_quota_reservations_across_connections(tmp_path, monkeypatch):
    clock = [100_000.0]
    monkeypatch.setattr(repository.time, "time", lambda: clock[0])
    path = tmp_path / "jobs.db"
    first, second = Repository(path), Repository(path)
    limits = AiBudget(tokens_per_minute=500, tokens_per_24h=750, requests_per_24h=3)
    with closing(first.db), closing(second.db):
        def reserve(repo, name, size):
            return repo.reserve_ai(name, "gemini", size, limits, False)
        state, call_id = reserve(first, "a", 480)
        assert state == "reserved" and reserve(second, "a", 480)[0] == "busy"
        assert reserve(second, "b", 100) == ("wait", 60)
        first.finish_ai(call_id, None, 260)
        assert reserve(second, "b", 200)[0] == "reserved"
        clock[0] += 61
        assert reserve(second, "c", 300)[0] == "budget"
        assert reserve(second, "c", 290)[0] == "reserved"
        assert tuple(first.ai_usage()) == (3, 750)
        clock[0] += 86401
        assert tuple(first.ai_usage()) == (0, 0)
        assert reserve(second, "c", 500)[0] == "reserved"


def test_429_suspends_other_jobs_until_retry_after(tmp_path, monkeypatch, job, decision):
    clock = [100_000.0]
    monkeypatch.setattr(repository.time, "time", lambda: clock[0])
    path = tmp_path / "jobs.db"
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "180"})
        return answer(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            async def review(posting):
                repo = Repository(path)
                with closing(repo.db):
                    return await assess(client, "gemini", "SQL", posting, decision, repo=repo)
            assert (await review(job)).ai_unavailable
            clock[0] += 130
            other = job.model_copy(update={"title": "Outra vaga júnior"})
            assert (await review(other)).ai_unavailable and len(attempts) == 1
            clock[0] += 51
            assert (await review(other)).analysis and len(attempts) == 2
    asyncio.run(run())


def test_invalid_classification_falls_back_with_reported_usage(tmp_path, job, decision):
    def respond(request):
        payload = answer(request).json()
        payload["answers"]["fit"]["score"] = 5
        return httpx.Response(200, json=payload)

    async def run():
        repo = Repository(tmp_path / "jobs.db")
        with closing(repo.db):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                local = await assess(client, "gemini", "SQL", job, decision, repo=repo)
            assert local.ai_unavailable and local.eligible and local.score == 7
            assert tuple(repo.ai_usage()) == (1, 517)
    asyncio.run(run())


def test_compact_context_selects_late_requirements_and_removes_contacts(job, decision):
    job.description = "\n".join(f"Informações sobre o escritório {i}. " * 6 for i in range(100))
    job.description += "\nRequisito obrigatório: SQL e Kubernetes.\nRequisito obrigatório: SQL e Kubernetes."
    decision.evidence = [Evidence(kind="Projeto", fact="Criei uma API. https://projeto.test", source="Pasta privada", terms=["SQL"])]
    profile = "# Nome Sobrenome\nCurso de SQL. contato@exemplo.test +55 (11) 99999-1234\n" + "Portfólio. " * 300
    context = compact_input(profile, job, decision)
    assert len(context["descricao"]) <= 2600 and context["descricao"].count("SQL e Kubernetes") == 1
    assert len(context["curriculo"]) <= 1050 and "SQL" in context["curriculo"]
    serialized = json.dumps(context, ensure_ascii=False)
    assert all(s not in serialized for s in ["Nome Sobrenome", "contato@", "99999", "projeto.test", "Pasta privada"])
