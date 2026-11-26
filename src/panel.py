import json
from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from tempfile import NamedTemporaryFile

from src.core.config import Settings
from src.core.models import Decision, JobPosting
from src.filters.workplace import workplace
from src.storage.repository import Repository

SOURCE_NAMES = {"amazon": "Amazon Careers", "ashby": "Ashby", "greenhouse": "Greenhouse", "himalayas": "Himalayas",
                "weworkremotely": "We Work Remotely", "comunidades": "Comunidades no Telegram", "hackernews": "Hacker News",
                "web": "Busca na web", "programathor": "ProgramaThor", "gupy": "Gupy", "github": "GitHub", "linkedin": "LinkedIn",
                "indeed": "Indeed", "infojobs": "InfoJobs", "vagascom": "Vagas.com", "nube": "Nube"}


def card(row, *, in_last_hunt: bool = False) -> str:
    job = JobPosting.model_validate_json(row["posting"])
    decision = Decision.model_validate_json(row["decision"])
    date = datetime.fromisoformat(row["eligible_at"]).astimezone()
    score = f"{decision.score:.1f}".replace(".", ",")
    company = escape(job.company or "Empresa não informada")
    mode, signal = workplace(job.profile_details)
    location = {"remote": "Remoto", "hybrid": "Híbrido", "onsite": "Presencial", "unknown": "Local a confirmar"}[mode]
    if job.location and job.location.casefold() not in (location.casefold(), "remote"):
        location += " (" + job.location + ")"
    tags = "".join(f'<li>{escape(s)}</li>' for s in decision.skills)
    facts = [f"Modalidade · {signal}", *(f"{e.kind} · {e.fact}" for e in decision.evidence)]
    if decision.analysis:
        facts.append(f"Parecer da IA · {decision.analysis.resumo_curto}")
        facts.append(f"Aderência estimada pela IA · {decision.analysis.score:g}/10 · nível {decision.analysis.senioridade_real}")
        facts.extend("A confirmar · " + s for s in decision.analysis.red_flags)
        if decision.analysis.skills_faltantes:
            facts.append("Lacunas identificadas · " + ", ".join(decision.analysis.skills_faltantes))
    facts.append("Nota do painel calculada pelas regras do perfil")
    details = "".join(f"<li>{escape(fact)}</li>" for fact in facts)
    link = escape(job.url, quote=True)
    form = bool(job.active_form)
    form_link = ""
    form_note = ""
    open_class, open_label = "open", "Abrir anúncio ↗"
    if form:
        if job.form_url == job.url:
            open_class, open_label = "open form-link", "Abrir Google Forms ↗"
        else:
            form_link = (f'<a class="form-link" href="{escape(job.form_url, quote=True)}" target="_blank" '
                         'rel="noopener noreferrer">Abrir Google Forms ↗</a>')
        label = "aberto na consulta" if job.form_status == "open" else "indicado no anúncio"
        if job.form_status == "open" and job.form_checked_at:
            label += f" de {job.form_checked_at.astimezone():%d/%m/%Y}"
        form_note = f'<p class="form-note">Google Forms · {label}</p>'
        if job.form_status == "open" and job.form_details and job.form_details.title and job.form_details.title != job.title:
            form_note += f'<p class="meta">No formulário · {escape(job.form_details.title)}</p>'
    if job.precision == "first_seen":
        timing = " · Publicação sem data informada"
    elif job.precision == "updated" and job.updated_at:
        timing = f" · Atualização na fonte em {job.updated_at.astimezone():%d/%m/%Y}"
    elif job.published_at:
        timing = f" · Publicação em {job.published_at.astimezone():%d/%m/%Y}"
    else:
        timing = ""
    status = "" if row["status"] == "nova" else f' · {escape(row["status"].capitalize())}'
    return f"""<article class="job" data-time="{int(date.timestamp())}" data-score="{decision.score}" data-form="{int(form)}" data-last-hunt="{int(in_last_hunt)}">
      <div class="job-top"><p class="company">{company}</p><span class="score" aria-label="Aderência {score} de 10">{score}<small>/10</small></span></div>
      <h2><a href="{link}" target="_blank" rel="noopener noreferrer">{escape(job.title)}</a></h2>
      <p class="meta">{escape(location)} · {escape(SOURCE_NAMES.get(job.source, job.source))}{status}</p>
      {form_note}
      <ul class="tags">{tags}</ul>
      <p class="found">Encontrada em {date:%d/%m/%Y às %H:%M}{timing}</p>
      <div class="actions"><details><summary>Ver avaliação</summary><ul>{details}</ul></details>
      {form_link}
      <a class="{open_class}" href="{link}" target="_blank" rel="noopener noreferrer">{open_label}</a></div>
    </article>"""


def write_panel(repo: Repository, directory: Path, settings: Settings | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "painel.html"
    with repo.db:
        repo.db.execute("BEGIN")
        run = repo.last_hunt()
        rows = list(repo.visible_approved(settings.remote if settings else True))
        rows.sort(key=lambda row: not bool(JobPosting.model_validate_json(row["posting"]).active_form))
        last_ids = set(json.loads(run["result_ids"])) if run and run["result_ids"] is not None else None
        cards = [card(row, in_last_hunt=last_ids is not None and row["id"] in last_ids) for row in rows]
    coverage = ""
    if run:
        date = datetime.fromisoformat(run["finished"]).astimezone()
        updated = f"Última busca em {date:%d/%m/%Y às %H:%M}"
        summary = (f'{run["listings"]} leituras de anúncios · janela principal de {run["hours"]} h · '
                   f'{run["sources_ok"]} de {run["sources_total"]} fontes responderam.')
        if run["errors"]:
            summary += f' Consultas com erro: {run["errors"]}.'
        stats = json.loads(run["source_stats"])
        if stats:
            body = "".join(f'<tr><th>{escape(SOURCE_NAMES.get(s["source"], s["source"]))}</th><td>{s["read"]}</td><td>{s["approved"]}</td>'
                           f'<td>{s["forms"]}</td><td>{s["queries_ok"]}</td><td>{s["errors"]}</td></tr>' for s in stats)
            coverage = ('<details class="coverage"><summary>Fontes da última busca</summary><table><thead>'
                        '<tr><th>Fonte</th><th>Lidas</th><th>Aprovadas</th><th>Forms</th><th>Consultas OK</th>'
                        '<th>Erros</th></tr></thead><tbody>' + body + '</tbody></table>')
            if settings:
                calls, tokens = repo.ai_usage()
                budget = settings.ai_budget
                coverage += (f'<p>IA nas últimas 24 h · {calls} chamadas e {tokens} tokens registrados neste computador. '
                             f'Limites atuais de {budget.finalists_per_run} novos pareceres por busca e '
                             f'{budget.requests_per_24h} chamadas em 24 h.</p>')
            coverage += '</details>'
        empty = ("Nenhuma oportunidade passou pelos filtros até agora." if run["sources_ok"] else
                 "A última busca não conseguiu consultar os portais. Confira a conexão e abra “Abrir vagas” para tentar novamente.")
    else:
        updated = "Aguardando a primeira busca"
        summary = "Abra “Abrir vagas” para consultar os portais e atualizar o seu histórico."
        empty = "Seu histórico de oportunidades começa com a primeira coleta."
    template = Template(Path(__file__).with_name("panel.html").read_text(encoding="utf-8"))
    last_option = f'<option value="last" selected>Última busca ({run["hours"]} h)</option>' if last_ids is not None else ""
    html = template.substitute(updated=updated, summary=summary, coverage=coverage, empty=empty,
                               last_option=last_option, cards="\n".join(cards),
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
