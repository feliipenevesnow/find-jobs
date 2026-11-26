import asyncio
import json
from datetime import timedelta
from io import StringIO

import httpx
from rich.console import Console

from main import review_text
from src.core.models import Decision, Evidence
from src.filters.ai import assess
from src.filters.profile import extra_credit, read_profile, relevant_evidence
from src.filters.rules import evaluate
from src.notifiers.telegram import message


def entry(kind, fact, *terms):
    return Evidence(kind=kind, fact=fact, source="Ficha do projeto", terms=list(terms))


def test_profile_is_portable_and_plain_markdown_still_works(tmp_path):
    path = tmp_path / "perfil.md"
    assert read_profile(path) == ("", [])
    path.write_text("Currículo simples", encoding="utf-8")
    assert read_profile(path) == ("Currículo simples", [])
    path.write_text(
        "Trajetória\n\n## Evidências por requisito\n\n"
        "| Projeto | Integrei a equipe do projeto A. | Manual A, p. 3 | Angular, Prisma |\n"
        "## Disponibilidade\nRemoto\n", encoding="utf-8-sig",
    )
    narrative, records = read_profile(path)
    assert "Disponibilidade" in narrative and "Manual A" not in narrative
    assert records[0].source == "Manual A, p. 3" and records[0].kind == "Projeto"
    assert relevant_evidence("PRISMA", records)[0].terms == ["Prisma"]
    assert not relevant_evidence("Prismatic", records)


def test_evidence_bonus_deduplicates_and_respects_scope(settings, job, now):
    project = entry("Projeto", "Testei sensores.", "ESP32")
    course = entry("Formação", "Estudei microcontroladores.", "ESP32")
    unrelated = entry("Ensino", "Ministrei um curso de bancos.", "SQL")
    job = job.model_copy(update={"description": "Programação com ESP32"})
    result = evaluate(job, settings, now-timedelta(hours=2), now, records=[course, project, unrelated])
    assert result.score == 7 and result.evidence == [project]
    assert extra_credit(result.evidence, ["ESP32"]) == 0
    assert "Projeto: Testei sensores." in message(job, result, 2)
    assert Decision.model_validate_json(result.model_dump_json()).evidence == [project]
    assert Decision.model_validate_json('{"eligible":true,"reason":"registro antigo"}').evidence == []


def test_review_shows_evidence_even_when_mixed_level_is_blocked(settings, tmp_path):
    settings.resume = tmp_path / "perfil.md"
    settings.resume.write_text("Perfil\n## Evidências por requisito\n"
                               "| Ensino | Ministrei o curso. | Certificado | Spring Boot |\n", encoding="utf-8")
    settings.database = tmp_path / "vagas.sqlite3"
    path = tmp_path / "vaga.txt"
    path.write_text("Desenvolvedor JR/PL | Remoto\nAPIs com Spring Boot.", encoding="utf-8")
    output = StringIO()
    review_text(path, settings, Console(file=output, width=100))
    assert "Título misto" in output.getvalue() and "Ministrei o curso." in output.getvalue()
    assert not settings.database.exists()


def test_ai_preserves_and_receives_selected_facts(job, monkeypatch):
    proof = entry("Ensino", "Ministrei o curso.", "Spring Boot")
    decision = Decision(eligible=True, reason="perfil", evidence=[proof])
    monkeypatch.setenv("AI_MODEL", "teste")
    def respond(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert data["evidencias_selecionadas"] == [proof.model_dump()]
        result = dict(score=7.5, senioridade_real="junior", skills_match=[], skills_faltantes=[],
                      red_flags=[], resumo_curto="Há experiência de ensino.")
        return httpx.Response(200, json={"message": {"content": json.dumps(result)}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await assess(client, "ollama", "Perfil", job, decision)
            assert result.analysis.score == 7.5 and result.score == decision.score and result.evidence == [proof]
    asyncio.run(run())
