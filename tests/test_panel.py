import asyncio
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
from bs4 import BeautifulSoup
from rich.console import Console

import main
from src.core import hunt as runner
from src.core.models import Decision
from src.panel import write_panel
from src.storage.repository import Repository
from src.collectors.base import BaseCollector


def test_history_survives_age_filter_and_escapes_titles(tmp_path, job, settings, monkeypatch):
    now = datetime.now(timezone.utc)
    risky = job.model_copy(update={"title": "Desenvolvedor Jr <img src=x>", "published_at": now - timedelta(days=4)})
    repo = Repository(tmp_path / "vagas.db")
    class OlderBoard(BaseCollector):
        def queries(self):
            return ["dev"]
        async def fetch(self, query):
            yield risky

    monkeypatch.setattr(runner, "COLLECTORS", {"gupy": OlderBoard})
    settings.sources = ["gupy"]
    with closing(repo.db):
        approved = repo.record(risky, Decision(eligible=True, reason="perfil", score=8.5), now - timedelta(days=4))
        repo.claim(approved)
        repo.delivered(approved)  # Histórico de versões com notificações.
        repo.set_status(approved, "entrevista")
        repo.record(job.model_copy(update={"title": "Estágio comercial", "url": "https://example.test/outra"}),
                    Decision(eligible=False, reason="cargo"), now)
        assert asyncio.run(runner.hunt(settings, repo, 26, False, True, Console(file=StringIO()))) == 0
        panel = write_panel(repo, tmp_path)
    page = BeautifulSoup(panel.read_text(encoding="utf-8"), "html.parser")
    assert len(page.select(".job")) == 1
    assert page.select_one("h2").get_text() == risky.title and not page.select_one("img")
    assert page.select_one("a.open")["href"] == risky.url and "Entrevista" in page.select_one(".meta").text
    assert "1 de 1" in page.select_one(".summary").text


@pytest.mark.parametrize("first_ok, expected", [(True, 1), (False, 2)])
def test_daily_entry_point_reuses_success_and_retries_outage(tmp_path, monkeypatch, settings, first_ok, expected):
    settings.database = tmp_path / "vagas.db"
    monkeypatch.setattr(main.Settings, "read", lambda _: settings)
    calls = []
    async def collect(settings, repo, hours, dry, skip_ai, console, *, extend_forms):
        assert extend_forms
        ok = first_ok or bool(calls)
        calls.append(hours)
        repo.finish_hunt(hours, 0, int(ok), 1, int(not ok))
        return int(not ok)
    monkeypatch.setattr(main, "hunt", collect)
    monkeypatch.setattr(sys, "argv", ["main.py", "--diaria"])
    assert main.main() == int(not first_ok)
    assert main.main() == 0
    panel = settings.database.parent / "painel.html"
    assert len(calls) == expected and panel.is_file()
    assert "Nenhuma oportunidade passou" in panel.read_text(encoding="utf-8")


def test_chosen_period_starts_new_hunt_even_after_daily_success(tmp_path, monkeypatch, settings):
    settings.database = tmp_path / "vagas.db"
    monkeypatch.setattr(main.Settings, "read", lambda _: settings)
    calls = []

    async def collect(settings, repo, hours, dry, skip_ai, console, *, extend_forms):
        calls.append((hours, extend_forms, dry))
        if not dry:
            repo.finish_hunt(hours, 0, 1, 1, 0)
        return 0

    monkeypatch.setattr(main, "hunt", collect)
    entries = iter(["", "0", "721", "sete", "168"])
    monkeypatch.setattr("builtins.input", lambda _: next(entries))
    monkeypatch.setattr(sys, "argv", ["main.py", "--diaria", "--escolher-periodo"])
    assert main.main() == main.main() == 0
    monkeypatch.setattr(sys, "argv", ["main.py", "--dry-run", "--hours", "1"])
    assert main.main() == 0
    assert calls == [(settings.hours, True, False), (168, False, False), (1, False, True)]


def test_last_hunt_view_preserves_history_and_empty_manual_results(tmp_path, job, now):
    with closing((repo := Repository(tmp_path / "vagas.db")).db):
        decision = Decision(eligible=True, score=8, reason="ok")
        earlier = repo.record(job, decision, now - timedelta(days=7))
        other = job.model_copy(update={"url": "https://employer.test/1", "title": "Outra vaga de Dev Jr"})
        repo.record(other, decision, now)
        repo.finish_hunt(24, 1, 1, 1, 0, result_ids={earlier})
        page = BeautifulSoup(write_panel(repo, tmp_path).read_text(encoding="utf-8"), "html.parser")
        assert page.select_one("#period option[selected]").text == "Última busca (24 h)"
        assert page.select_one('[data-last-hunt="1"] h2').text == job.title
        assert len(page.select(".job")) == 2
        assert [r["id"] for r in repo.pending(now, result_ids={earlier})] == [earlier]
        repo.finish_hunt(1, 0, 1, 1, 0, result_ids=set())
        page = BeautifulSoup(write_panel(repo, tmp_path).read_text(encoding="utf-8"), "html.parser")
        assert page.select_one("#period option[selected]").text == "Última busca (1 h)"
        assert len(page.select('.job[data-last-hunt="0"]')) == 2
