from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from tempfile import NamedTemporaryFile

from src.core.models import Decision, JobPosting
from src.storage.repository import Repository


def card(row) -> str:
    job = JobPosting.model_validate_json(row["posting"])
    decision = Decision.model_validate_json(row["decision"])
    date = datetime.fromisoformat(row["eligible_at"]).astimezone()
    score = f"{decision.score:.1f}".replace(".", ",")
    company = escape(job.company or "Empresa não informada")
    location = escape("Remoto" if job.is_remote else job.location or "Local a confirmar")
    tags = "".join(f'<li>{escape(s)}</li>' for s in decision.skills)
    facts = [f"{e.kind} · {e.fact}" for e in decision.evidence]
    if decision.analysis:
        facts.insert(0, decision.analysis.resumo_curto)
        facts.extend("A confirmar · " + s for s in decision.analysis.red_flags)
        if decision.analysis.skills_faltantes:
            facts.append("Lacunas identificadas · " + ", ".join(decision.analysis.skills_faltantes))
    facts.append("Avaliação por IA" if decision.analysis else "Avaliação pelas regras do seu perfil")
    details = "".join(f"<li>{escape(fact)}</li>" for fact in facts)
    link = escape(job.url, quote=True)
    status = "" if row["status"] == "nova" else f' · {escape(row["status"].capitalize())}'
    return f"""<article class="job" data-time="{int(date.timestamp())}" data-score="{decision.score}">
      <div class="job-top"><p class="company">{company}</p><span class="score" aria-label="Aderência {score} de 10">{score}<small>/10</small></span></div>
      <h2><a href="{link}" target="_blank" rel="noopener noreferrer">{escape(job.title)}</a></h2>
      <p class="meta">{location} · {escape(job.source)}{status}</p>
      <ul class="tags">{tags}</ul>
      <p class="found">Encontrada em {date:%d/%m/%Y às %H:%M}</p>
      <div class="actions"><details><summary>Ver avaliação</summary><ul>{details}</ul></details>
      <a class="open" href="{link}" target="_blank" rel="noopener noreferrer">Abrir anúncio ↗</a></div>
    </article>"""


def write_panel(repo: Repository, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "painel.html"
    with repo.db:
        repo.db.execute("BEGIN")
        run = repo.last_hunt()
        cards = [card(row) for row in repo.approved()]
    if run:
        date = datetime.fromisoformat(run["finished"]).astimezone()
        updated = f"Última busca em {date:%d/%m/%Y às %H:%M}"
        summary = (f'{run["listings"]} anúncios analisados na janela de {run["hours"]} h · '
                   f'{run["sources_ok"]} de {run["sources_total"]} portais consultados responderam '
                   f'(Gupy, LinkedIn, Indeed, InfoJobs, Vagas.com, GitHub e Nube).')
        if run["errors"]:
            summary += f' Consultas com erro: {run["errors"]}.'
        empty = ("Nenhuma oportunidade passou pelos filtros até agora." if run["sources_ok"] else
                 "A última busca não conseguiu consultar os portais. Confira a conexão e abra “Abrir vagas” para tentar novamente.")
    else:
        updated = "Aguardando a primeira busca"
        summary = "Abra “Abrir vagas” para consultar os portais e atualizar o seu histórico."
        empty = "Seu histórico de oportunidades começa com a primeira coleta."
    template = Template(Path(__file__).with_name("panel.html").read_text(encoding="utf-8"))
    html = template.substitute(updated=updated, summary=summary, empty=empty, cards="\n".join(cards),
                               count=len(cards), no_cards="" if not cards else "hidden")
    out = NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False)
    tmp = Path(out.name)
    try:
        with out:
            out.write(html)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
