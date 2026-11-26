import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.collectors.base import Context
from src.collectors.careers import Amazon, Ashby, Greenhouse, Himalayas, ProgramaThor, WeWorkRemotely
from src.collectors.community import Communities
from src.collectors.github import Github
from src.collectors.gupy import Gupy
from src.collectors.hackernews import HackerNews
from src.collectors.jobspy import JobSpy
from src.collectors.portals import HtmlBoard, Nube
from src.collectors.public_page import public_page
from src.collectors.web import WebSearch, form_state
from src.core.config import Settings
from src.core.forms import application_form, google_form_url
from src.core.models import Decision, JobPosting, plain
from src.filters.ai import PRIORITY, assess
from src.filters.profile import read_profile
from src.filters.rules import TIME_FILTERS, application_rejection, evaluate
from src.storage.repository import Repository

COLLECTORS = {"gupy": Gupy, "github": Github, "linkedin": JobSpy, "indeed": JobSpy,
              "infojobs": HtmlBoard, "vagascom": HtmlBoard, "nube": Nube, "programathor": ProgramaThor, "amazon": Amazon,
              "greenhouse": Greenhouse, "ashby": Ashby, "himalayas": Himalayas,
              "comunidades": Communities, "web": WebSearch, "hackernews": HackerNews, "weworkremotely": WeWorkRemotely}
log = logging.getLogger("busca")


def print_evidence(console: Console, decision: Decision):
    for item in decision.evidence:
        console.print(Text(f"  {item.kind} · {', '.join(item.terms)}", style="bold"))
        console.print(Text(f"    {item.fact}"))
        console.print(Text(f"    Fonte: {item.source}", style="dim"))


def print_ai_usage(console: Console, repo: Repository, settings: Settings):
    calls, tokens = repo.ai_usage()
    console.print(f"IA remota · últimas 24 h neste banco · {calls}/{settings.ai_budget.requests_per_24h} chamadas "
                  f"e {tokens}/{settings.ai_budget.tokens_per_24h} tokens contabilizados.")


async def check_form(client, url):
    try:
        final, soup = await public_page(client, url)
        destination = urlsplit(final)
        if destination.hostname == "docs.google.com" and destination.path.endswith("/closedform"):
            return url, "closed"
        if google_form_url(final) and destination.hostname == "docs.google.com":
            return google_form_url(final) or url, form_state(soup)
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (404, 410):
            return url, "closed"
    except (ValueError, OSError, httpx.HTTPError):
        pass
    # Bloqueio temporário ou login: o anúncio continua sendo a evidência do link.
    return url, "listed"


async def verify_form(job, client, checks):
    if job.form_url not in checks:
        checks[job.form_url] = asyncio.create_task(check_form(client, job.form_url))
    job.form_url, job.form_status = await checks[job.form_url]
    job.form_checked_at = datetime.now(timezone.utc)


async def refresh_saved_forms(ctx, repo, checks):
    for row in repo.forms_due(ctx.form_cutoff, ctx.now - timedelta(hours=12), ctx.settings.results_per_search):
        job = JobPosting.model_validate_json(row["posting"])
        await verify_form(job, ctx.client, checks)
        decision = Decision.model_validate_json(row["decision"])
        if job.form_status == "closed" and (reason := application_rejection(job)):
            decision = decision.model_copy(update={"eligible": False, "reason": reason})
        repo.record(job, decision, ctx.now)


async def review_finalists(client, candidates, provider, resume, repo, settings, dry):
    remaining = settings.ai_budget.finalists_per_run
    for job, local in sorted(candidates, key=lambda pair: (not bool(pair[0].active_form), pair[0].form_status != "open",
                                                           -pair[1].score, pair[0].url)):
        options = dict(repo=repo, limits=settings.ai_budget)
        result = await assess(client, provider, resume, job, local, preview=True, **options)
        if not result.analysis and not dry:
            detail = plain(job.description)
            if remaining == 0 or len(detail.split()) < 8 or not (local.skills or local.evidence or PRIORITY.search(detail)):
                continue
            remaining -= 1
            result = await assess(client, provider, resume, job, local, **options)
        repo.record(job, result, datetime.now(timezone.utc))


async def hunt(settings: Settings, repo: Repository, hours: int, dry: bool, skip_ai: bool,
               console: Console) -> int:
    now = datetime.now(timezone.utc)
    provider = "none" if skip_ai else (os.getenv("AI_PROVIDER") or "none").strip().lower()
    if provider not in {"none", "gemini", "groq", "openai", "ollama"}:
        raise ValueError("AI_PROVIDER inválido no .env")
    try:
        resume, records = read_profile(settings.resume)
    except OSError:
        resume, records = "", []
    if not resume and provider != "none":
        log.warning("Currículo indisponível. A rodada utilizará a nota por regras.")
    console.print(f"[bold cyan]Busca de vagas[/]  ·  {hours} h  ·  " +
                  ("prévia" if dry else "coleta") + f"  ·  IA {provider}")
    user_agent = "Mozilla/5.0 (compatible; FindJobsBR/0.1; public job listings)"
    async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=8), follow_redirects=True,
                                 headers={"User-Agent": user_agent}) as public, httpx.AsyncClient(timeout=35) as private:
        ctx = Context(settings, now, hours, repo.checkpoint("nube"), repo.seen_urls("nube"), public)
        sources = [COLLECTORS[name](name, ctx) for name in dict.fromkeys(settings.sources)]
        form_checks = {}
        candidate_ids = set()

        async def ingest(collector):
            count = 0
            accepted = set()
            forms = set()
            async for job in collector.collect():
                count += 1
                observed = datetime.now(timezone.utc)
                if not job.form_url and job.source != "himalayas":
                    job.form_url = application_form(job.description)
                first_seen = observed
                if job.precision == "first_seen" and (matches := repo.lookup(job)):
                    first_seen = min(datetime.fromisoformat(row["collected_at"]) for row in matches)
                if job.form_url and job.form_status == "listed":
                    prospect = job.model_copy(update={"form_status": "open"})
                    fit = evaluate(prospect, settings, ctx.cutoff, observed, ctx.last_nube, records, first_seen=first_seen)
                    if fit.eligible and fit.score >= settings.min_score:
                        await verify_form(job, public, form_checks)
                elif job.form_status == "open":
                    job.form_checked_at = observed
                decision = evaluate(job, settings, ctx.cutoff, observed, ctx.last_nube, records, first_seen=first_seen)
                if decision.eligible and decision.score < settings.min_score:
                    decision = decision.model_copy(update={"eligible": False, "reason": "Nota abaixo do mínimo"})
                job_id = repo.record(job, decision, observed, keep_approved=decision.reason in TIME_FILTERS)
                if decision.eligible:
                    candidate_ids.add(job_id)
                    accepted.add(job_id)
                    if job.active_form:
                        forms.add(job_id)
            if collector.name == "nube" and collector.successes and not collector.errors:
                repo.complete("nube", now)
            return {"source": collector.name, "read": count, "approved": len(accepted), "forms": len(forms),
                    "queries_ok": collector.successes, "errors": collector.errors}

        stats = await asyncio.gather(*(ingest(c) for c in sources))
        await refresh_saved_forms(ctx, repo, form_checks)
        if provider != "none" and resume:
            candidates = []
            for job_id in candidate_ids:
                row = repo.db.execute("SELECT posting,decision FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row and (decision := Decision.model_validate_json(row["decision"])).eligible:
                    candidates.append((JobPosting.model_validate_json(row["posting"]), decision))
            await review_finalists(private, candidates, provider, resume, repo, settings, dry)
        summary = Table("Fonte", "Lidas", "Aprovadas", "Com Forms", "Erros", header_style="cyan", expand=False)
        for stat in stats:
            summary.add_row(stat["source"], *(str(stat[key]) for key in ("read", "approved", "forms", "errors")))
        console.print(summary)
        successful = sum(bool(source.successes) for source in sources)
        if not dry:
            repo.finish_hunt(hours, sum(s["read"] for s in stats), successful, len(sources),
                             sum(source.errors for source in sources), stats)
        rows = repo.pending(ctx.cutoff)
        total = 0
        for row in rows:
            job = JobPosting.model_validate_json(row["posting"])
            decision = Decision.model_validate_json(row["decision"])
            if not decision.eligible or decision.score < settings.min_score:
                continue
            current = evaluate(job, settings, ctx.cutoff, datetime.now(timezone.utc), now if job.precision == "discovery" else None,
                               records, first_seen=datetime.fromisoformat(row["collected_at"]))
            if not current.eligible:
                continue
            total += 1
            console.print(Text(f"#{row['id']}  {decision.score:.1f}/10  {job.title}  ·  {job.company or job.source}", style="bold"))
            console.print(Text(f"  {job.source}  ·  {job.url}", style="cyan"))
            if job.active_form:
                console.print(Text(f"  Candidatura por Google Forms · {job.active_form}", style="green"))
            if decision.skills:
                console.print(Text("  Tecnologias em comum: " + ", ".join(decision.skills)))
            print_evidence(console, decision)
            if decision.ai_unavailable:
                console.print(Text("  [Análise IA indisponível]"))
        console.print(f"{total} oportunidade(s) ativa(s) na janela da busca.")
        if provider not in ("none", "ollama"):
            print_ai_usage(console, repo, settings)
            if dry:
                console.print("Na prévia, vagas já analisadas usam a avaliação salva e vagas novas usam as regras locais.")
            elif not (os.getenv(provider.upper() + "_API_KEY") or "").strip():
                console.print(Text(f"Para ativar novas análises, preencha {provider.upper()}_API_KEY no .env."))
        if not successful:
            log.error("Nenhuma consulta foi concluída com sucesso.")
            return 1
        return 0
