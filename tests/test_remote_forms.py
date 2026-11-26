import asyncio
from contextlib import closing
from datetime import timedelta

import httpx
import pytest
from bs4 import BeautifulSoup

from src.core.forms import application_form, google_form_url
from src.core.hunt import review_finalists
from src.core.models import Analysis, Decision
from src.filters.rules import evaluate, evaluate_fit
from src.panel import write_panel
from src.storage.repository import Repository


@pytest.mark.parametrize("title,location,description,flag,allowed", [
    ("Dev Jr", "Curitiba", "Benefícios: auxílio home office.", False, False),
    ("Dev Jr", "Curitiba", "O processo seletivo é remoto.", False, False),
    ("Dev Jr", "Curitiba", "Processo seletivo 100% remoto.", False, False),
    ("Dev Jr", "Brasil", "Atendimento remoto a clientes.", False, False),
    ("Dev Jr", "Brasil", "Modelo de trabalho: 100% remoto.", False, True),
    ("Dev Jr", "", "Local\nRemoto\nComo se candidatar", False, True),
    ("Dev Jr", "", "- [ ] Presencial\n- [x] Remoto", False, True),
    ("Dev Jr", "", "- [ ] Remoto\n- [x] Presencial", False, False),
    ("Dev Jr", "Curitiba", "Não é 100% remoto.", True, False),
    ("Dev Jr", "", "Trabalho híbrido com auxílio home office.", True, False),
    ("Dev Jr", "", "100% remoto. Encontros presenciais mensais.", True, False),
    ("Dev Jr", "", "100% remoto, com idas eventuais ao escritório.", True, False),
    ("Dev Jr", "", "Remote role. 2 days per week in the office.", True, False),
    ("Dev Jr", "", "Remote role. Office visits are optional.", True, True),
    ("Dev Jr Hybrid Cloud", "Remote - Brazil", "Deploy hybrid cloud applications.", False, True),
    ("Dev Jr", "Remote (United States)", "", True, False),
    ("Dev Jr", "Remote - LATAM", "", True, True),
    ("Dev Jr", "", "100% remoto. US-only.", True, False),
    ("Dev Jr", "Brasil", "Remote role; hybrid work required.", True, False),
])
def test_remote_evidence(job, settings, title, location, description, flag, allowed):
    posting = job.model_copy(update=dict(title=title, location=location, is_remote=flag, description=description))
    assert evaluate_fit(posting, settings).eligible is allowed


def test_form_links_belong_to_application():
    short = "https://forms.gle/Acme123"
    long = "https://docs.google.com/forms/d/e/Some_ID/viewform"
    assert google_form_url(long + "?entry.100=email&usp=sharing") == long
    assert google_form_url("https://docs.google.com/forms/d/e/Some_ID") == long
    assert google_form_url("https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fforms.gle%2FAcme123") == short
    assert application_form("Interessados preencher:\nhttps://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fforms.gle%2FAcme123") == short
    assert application_form("Como se candidatar\n[Inscreva-se aqui](" + short + ")") == short
    assert application_form("Vaga de dev jr\nRequisitos: Python\n🔗 " + long) == long
    assert not application_form("Pesquisa de satisfação\nPreencha aqui: " + short)
    for unsafe in (short.replace("forms.gle", "forms.gle.example"), long.replace("viewform", "edit"),
                   long.replace("/viewform", "/edit"), short.replace("forms.gle", "user@forms.gle"),
                   short.replace("https:", "http:")):
        assert not google_form_url(unsafe)
    assert not application_form(f"Várias vagas. Candidate-se: {short} ou {long}")


def test_real_dates_and_open_form_window(job, settings, now):
    old = job.model_copy(update={"published_at": now - timedelta(days=12), "form_url": "https://forms.gle/abc"})
    cutoff = now - timedelta(hours=36)
    assert not evaluate(old, settings, cutoff, now).eligible
    old.form_status = "open"
    assert evaluate(old, settings, cutoff, now).eligible
    assert not evaluate(old.model_copy(update={"published_at": now - timedelta(days=31)}), settings, cutoff, now).eligible
    undated = old.model_copy(update={"published_at": None, "precision": "first_seen"})
    assert evaluate(undated, settings, cutoff, now, first_seen=now).eligible
    assert not evaluate(undated, settings, cutoff, now, first_seen=now - timedelta(days=2)).eligible


def test_finalists_prioritize_forms_reuse_cache_and_have_no_veto(tmp_path, monkeypatch, settings, job, now):
    settings.ai_budget.finalists_per_run = 1
    monkeypatch.setenv("AI_MODEL", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    decision = Decision(eligible=True, score=8, skills=["Python"], reason="Regras locais")
    employer = job.model_copy(update={"description": "Requisito obrigatório: desenvolver serviços web com Python e SQL."})
    form = employer.model_copy(update={"title": "Dev JR remoto na Startup", "url": "https://startup.example/vaga",
                                       "form_url": "https://forms.gle/ok", "form_status": "open"})
    sparse = employer.model_copy(update={"title": "Dev Jr", "url": "https://example.org/vaga", "description": "Python."})
    poor = Analysis(score=0, senioridade_real="senior", skills_match=[], skills_faltantes=["React"],
                    red_flags=["Confirmar requisito"], resumo_curto="Faltam detalhes.")
    calls = []

    def respond(request):
        import json
        body = json.loads(request.content)
        calls.append(json.loads(body["messages"][1]["content"])["titulo"])
        return httpx.Response(200, json={"choices": [{"message": {"content": poor.model_dump_json()}}]})

    async def run():
        repo = Repository(tmp_path / "jobs.db")
        with closing(repo.db):
            pairs = [(employer, decision.model_copy(update={"score": 9})), (sparse, decision), (form, decision)]
            for posting, result in pairs:
                repo.record(posting, result, now)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await review_finalists(client, pairs, "groq", "Experiência em Python", repo, settings, False)
                assert calls == [form.title]
                await review_finalists(client, pairs, "groq", "Experiência em Python", repo, settings, False)
                assert calls == [form.title, employer.title]
                await review_finalists(client, pairs, "groq", "Experiência em Python", repo, settings, True)
                assert len(calls) == 2
            rows = list(repo.visible_approved())
            assert len(rows) == 3
            reviewed = Decision.model_validate_json(repo.lookup(form)[0]["decision"])
            assert reviewed.analysis.senioridade_real == "senior" and reviewed.score == 8 and reviewed.skills == ["Python"]
            page = BeautifulSoup(write_panel(repo, tmp_path).read_text(encoding="utf-8"), "html.parser")
            assert page.select_one(".job .form-link")["href"] == form.form_url
            assert page.select_one(".job a.open")["href"] == form.url
            assert "aberto na consulta" in page.select_one(".form-note").text
    asyncio.run(run())


def test_old_history_recovers_ai_rejection_preserves_status_and_filters_modality(tmp_path, settings, job, now):
    settings.database = tmp_path / "legacy.db"
    with closing((repo := Repository(settings.database)).db):
        local_only = job.model_copy(update={"url": "https://a.test/1", "is_remote": False, "description": "Presencial."})
        old_id = repo.record(local_only, Decision(eligible=True, score=8, reason="antiga"), now)
        repo.set_status(old_id, "entrevista")
        rejected = Decision(eligible=False, score=1, reason="Senioridade acima do perfil pela IA",
                            analysis=Analysis(score=1, senioridade_real="senior", skills_match=[], skills_faltantes=[],
                                              red_flags=[], resumo_curto="Confirmar nível."))
        good_id = repo.record(job, rejected, now)
    with closing((repo := Repository.for_settings(settings)).db):
        visible = list(repo.visible_approved())
        assert [r["id"] for r in visible] == [good_id]
        assert repo.lookup(local_only)[0]["status"] == "entrevista"
        repo.set_status(good_id, "salva")
        changed = job.model_copy(update={"workplace": "hybrid"})
        repo.record(changed, evaluate_fit(changed, settings), now)
        assert not list(repo.visible_approved())
        assert repo.lookup(changed)[0]["status"] == "salva"


def test_refresh_closed_form_preserves_an_independent_career_page(tmp_path, settings, job, now, monkeypatch):
    from src.collectors.base import Context
    from src.core import hunt as runner
    calls = []
    async def closed(client, url):
        calls.append(url)
        return url, "closed"
    monkeypatch.setattr(runner, "check_form", closed)
    form = "https://forms.gle/closed"
    ctx = Context(settings, now, 36, None, set(), None)
    with closing((repo := Repository(tmp_path / "db")).db):
        direct = job.model_copy(update={"url": form, "form_url": form, "source": "web", "precision": "first_seen"})
        portal = job.model_copy(update={"form_url": form, "title": "Outro Dev Jr"})
        for item in (direct, portal):
            repo.record(item, Decision(eligible=True, reason="local", score=8), now)
        asyncio.run(runner.refresh_saved_forms(ctx, repo, {}))
        assert calls == [form]
        rows = list(repo.visible_approved())
        assert len(rows) == 1 and rows[0]["url"] == portal.url
        assert "Abrir Google Forms" not in write_panel(repo, tmp_path).read_text(encoding="utf-8")
