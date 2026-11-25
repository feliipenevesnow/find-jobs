import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.collectors.base import Context
from src.collectors.github import Github
from src.collectors.gupy import Gupy
from src.collectors.jobspy import JobSpy
from src.collectors.portals import HtmlBoard, Nube
from src.core.config import Settings
from src.core.models import Decision, JobPosting
from src.filters.ai import assess
from src.filters.profile import read_profile
from src.filters.rules import TIME_FILTERS, evaluate
from src.storage.repository import Repository

COLLECTORS = {"gupy": Gupy, "github": Github, "linkedin": JobSpy, "indeed": JobSpy,
              "infojobs": HtmlBoard, "vagascom": HtmlBoard, "nube": Nube}
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
        review_lock = asyncio.Lock()

        async def ingest(collector):
            count = 0
            async for job in collector.collect():
                count += 1
                observed = datetime.now(timezone.utc)
                decision = evaluate(job, settings, ctx.cutoff, observed, ctx.last_nube, records)
                if decision.eligible and provider != "none":
                    async with review_lock:
                        decision = await assess(private, provider, resume, job, decision,
                                                repo=repo, limits=settings.ai_budget, preview=dry)
                if decision.eligible and decision.score < settings.min_score:
                    decision = decision.model_copy(update={"eligible": False, "reason": "Nota abaixo do mínimo"})
                repo.record(job, decision, observed, keep_approved=decision.reason in TIME_FILTERS)
            if collector.name == "nube" and collector.successes and not collector.errors:
                repo.complete("nube", now)
            return count

        counts = await asyncio.gather(*(ingest(c) for c in sources))
        summary = Table("Fonte", "Lidas", "Consultas com erro", header_style="cyan", expand=False)
        for source, count in zip(sources, counts):
            summary.add_row(source.name, str(count), str(source.errors))
        console.print(summary)
        successful = sum(bool(source.successes) for source in sources)
        if not dry:
            repo.finish_hunt(hours, sum(counts), successful, len(sources), sum(source.errors for source in sources))
        rows = repo.pending(ctx.cutoff)
        total = 0
        for row in rows:
            job = JobPosting.model_validate_json(row["posting"])
            decision = Decision.model_validate_json(row["decision"])
            if job.precision in {"day", "timestamp"} and job.published_at and job.published_at < ctx.cutoff:
                continue
            if decision.score < settings.min_score:
                continue
            current = evaluate(job, settings, ctx.cutoff, datetime.now(timezone.utc), now if job.precision == "discovery" else None, records)
            if not current.eligible:
                continue
            total += 1
            console.print(Text(f"#{row['id']}  {decision.score:.1f}/10  {job.title}  ·  {job.company or job.source}", style="bold"))
            console.print(Text(f"  {job.source}  ·  {job.url}", style="cyan"))
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
