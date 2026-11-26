import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.collectors.base import Context
from src.collectors.careers import Amazon, Ashby, Greenhouse, Himalayas, ProgramaThor, WeWorkRemotely
from src.collectors.community import Communities
from src.collectors.github import Github
from src.collectors.google_forms import read_form
from src.collectors.gupy import Gupy
from src.collectors.hackernews import HackerNews
from src.collectors.jobspy import JobSpy
from src.collectors.portals import HtmlBoard, Nube
from src.collectors.web import WebSearch
from src.core.config import Settings
from src.core.forms import application_form, google_form_url
from src.core.models import Decision, JobPosting, plain
from src.filters.ai import PRIORITY, assess
from src.filters.profile import read_profile
from src.filters.rules import TIME_FILTERS, evaluate, evaluate_application, publication_rejection
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


async def verify_form(job, ctx, checks, repo):
    link = job.form_url
    if link not in checks:
        checks[link] = asyncio.create_task(read_form(ctx.client, link, ctx.settings.roles))
    form = await checks[link]
    checks.setdefault(form.url, checks[link])
    job.form_listed_url = job.form_listed_url or link
    job.form_url, job.form_status, job.form_details = form.url, form.status, form.details
    job.form_checked_at = datetime.now(timezone.utc)
    if form.status == "listed":
        for row in repo.lookup(job):
            previous = JobPosting.model_validate_json(row["posting"])
            if previous.form_url and link in (previous.form_url, previous.form_listed_url or application_form(previous.description)):
                job.form_url, job.form_details = previous.form_url, previous.form_details
                if previous.form_status in ("closed", "unrelated"):
                    job.form_status = previous.form_status
                break
    if job.source == "web" and google_form_url(job.url) and form.status == "open":
        if form.job_opening:
            job.title, job.description = form.details.title, form.details.description
        else:
            job.form_status = "unrelated"


async def refresh_saved_forms(ctx, repo, checks, records=None):
    for row in repo.forms_due(ctx.form_cutoff, ctx.now - timedelta(hours=12), ctx.settings.results_per_search):
        job = JobPosting.model_validate_json(row["posting"])
        await verify_form(job, ctx, checks, repo)
        repo.record(job, evaluate_application(job, ctx.settings, records), ctx.now)


async def review_finalists(client, candidates, provider, resume, repo, settings, dry):
    remaining = settings.ai_budget.finalists_per_run
    for job, local in sorted(candidates, key=lambda pair: (not bool(pair[0].active_form), pair[0].form_status != "open",
                                                           -pair[1].score, pair[0].url)):
        options = dict(repo=repo, limits=settings.ai_budget)
        result = await assess(client, provider, resume, job, local, preview=True, **options)
        if not result.analysis and not dry:
            detail = plain(job.profile_details.description)
            if remaining == 0 or len(detail.split()) < 8 or not (local.skills or local.evidence or PRIORITY.search(detail)):
                continue
            remaining -= 1
            result = await assess(client, provider, resume, job, local, **options)
        repo.record(job, result, datetime.now(timezone.utc))


async def hunt(settings: Settings, repo: Repository, hours: int, dry: bool, skip_ai: bool,
               console: Console, *, extend_forms: bool = True) -> int:
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
        ctx = Context(settings, now, hours, repo.checkpoint("nube"), repo.seen_urls("nube"), public, extend_forms)
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
                if job.form_url and job.precision == "unknown" and not job.published_at:
                    job.precision = "first_seen"
                first_seen = observed
                if job.precision == "first_seen" and (matches := repo.lookup(job)):
                    first_seen = min(datetime.fromisoformat(row["collected_at"]) for row in matches)
                if job.form_url and job.form_status == "listed":
                    if not publication_rejection(job, ctx.cutoff, observed, ctx.last_nube, first_seen, ctx.form_cutoff):
                        await verify_form(job, ctx, form_checks, repo)
                elif job.form_status == "open":
                    job.form_checked_at = observed
                decision = evaluate(job, settings, ctx.cutoff, observed, ctx.last_nube, records,
                                    first_seen=first_seen, form_cutoff=ctx.form_cutoff)
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
        await refresh_saved_forms(ctx, repo, form_checks, records)
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
        result_ids = None if extend_forms else candidate_ids
        if not dry:
            repo.finish_hunt(hours, sum(s["read"] for s in stats), successful, len(sources),
                             sum(source.errors for source in sources), stats, result_ids=result_ids)
        rows = repo.pending(ctx.cutoff, result_ids=result_ids)
        total = 0
        for row in rows:
            job = JobPosting.model_validate_json(row["posting"])
            decision = Decision.model_validate_json(row["decision"])
            if not decision.eligible or decision.score < settings.min_score:
                continue
            current = evaluate(job, settings, ctx.cutoff, datetime.now(timezone.utc), now if job.precision == "discovery" else None,
                               records, first_seen=datetime.fromisoformat(row["collected_at"]), form_cutoff=ctx.form_cutoff)
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
