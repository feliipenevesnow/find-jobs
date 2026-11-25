import argparse
import asyncio
import logging
import math
import os
import sqlite3
import sys
import webbrowser
from contextlib import closing
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.core.config import Settings
from src.core.hunt import hunt, print_ai_usage, print_evidence
from src.core.models import JobDetails, JobPosting, normalized
from src.filters.profile import read_profile
from src.filters.rules import evaluate_fit
from src.panel import write_panel
from src.storage.repository import Repository

ROOT = Path(__file__).resolve().parent
STATUSES = ("nova", "salva", "candidatado", "entrevista", "descartada")


def review_text(path: Path, settings: Settings, console: Console) -> None:
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("Informe o título na primeira linha do TXT e a descrição nas seguintes.")
    title, *body = lines
    # Uma linha curta logo após o título pode identificar a localização ou a modalidade.
    location_lines = []
    for line in body[:2]:
        if len(line) > 80 or line.endswith("."):
            break
        location_lines.append(line)
    labels = {normalized(s) for s in [*location_lines, *title.split("|")]}
    job = JobDetails(title=title, description="\n".join(body), source="arquivo",
                     location=" ".join(location_lines),
                     is_remote=bool(labels & {"remoto", "remota", "remote", "home office", "100 remoto"}))
    _, records = read_profile(settings.resume)
    decision = evaluate_fit(job, settings, records)
    console.print(Text(job.title, style="bold"))
    if decision.eligible and decision.score >= settings.min_score:
        console.print(Text(f"Aprovada pelas regras · {decision.score:.1f}/10"))
    elif decision.eligible:
        console.print(Text(f"Nota abaixo do mínimo · {decision.score:.1f}/10"))
    else:
        console.print(Text("Filtrada · " + decision.reason))
    if decision.evidence:
        print_evidence(console, decision)
    else:
        console.print("Nenhuma evidência específica encontrada no perfil para os termos da vaga.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Encontre vagas de entrada em desenvolvimento.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml", help="Arquivo YAML de perfil")
    parser.add_argument("--hours", type=int, metavar="HORAS", help="Janela de publicação, de 1 a 720 horas")
    parser.add_argument("--skip-ai", action="store_true", help="Usar somente regras e stack")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Simular a coleta em memória")
    mode.add_argument("--diaria", action="store_true", help="Coletar se ainda não houve uma busca bem-sucedida hoje")
    mode.add_argument("--alarme", choices=("manha", "noite"), help="Executar janela automática da manhã (dia anterior) ou noite (dia atual)")
    mode.add_argument("--painel", action="store_true", help="Gerar o painel com o histórico já salvo")
    parser.add_argument("--abrir", action="store_true", help="Abrir o painel no navegador ao concluir")
    mode.add_argument("--list", action="store_true", help="Mostrar as últimas 50 oportunidades aprovadas")
    mode.add_argument("--uso-ia", action="store_true", help="Mostrar chamadas e tokens das últimas 24 horas neste computador")
    mode.add_argument("--avaliar-arquivo", type=Path, metavar="VAGA.txt", help="Conferir perfil e evidências para um anúncio em texto, localmente")
    mode.add_argument("--status", nargs=2, metavar=("ID", "STATUS"), help="Atualizar a candidatura pelo ID numérico")
    args = parser.parse_args()
    if args.hours is not None and not 1 <= args.hours <= 720:
        parser.error("--hours deve estar entre 1 e 720")
    if args.status and (not args.status[0].isdigit() or args.status[1] not in STATUSES):
        parser.error("--status requer ID numérico e um dos valores: " + ", ".join(STATUSES))
    if args.abrir and (args.dry_run or args.list or args.uso_ia or args.avaliar_arquivo or args.status):
        parser.error("--abrir acompanha a coleta, --diaria ou --painel")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s · %(name)s · %(message)s")
    load_dotenv(ROOT / ".env", override=False)
    console = Console(highlight=False)
    repo = None
    try:
        settings = Settings.read(args.config.resolve())
        if args.avaliar_arquivo:
            review_text(args.avaliar_arquivo, settings, console)
            return 0
        repo = Repository(settings.database, preview=args.dry_run or args.list or args.uso_ia or args.painel)
        if args.uso_ia:
            print_ai_usage(console, repo, settings)
            return 0
        if args.list:
            table = Table("ID", "Vaga", "Fonte", "Candidatura", "Anúncio", header_style="cyan")
            for row in repo.approved().fetchmany(50):
                job = JobPosting.model_validate_json(row["posting"])
                table.add_row(str(row["id"]), Text(job.title), job.source, row["status"], Text(job.url))
            console.print(table)
            return 0
        if args.status:
            ok = repo.set_status(int(args.status[0]), args.status[1])
            if ok:
                write_panel(repo, settings.database.parent)
            console.print("Registro atualizado." if ok else "Nenhum registro aplicável para esse ID.")
            return 0 if ok else 1
        hours = args.hours or settings.hours
        if args.dry_run:
            return asyncio.run(hunt(settings, repo, hours, True, args.skip_ai, console))
        code = 0
        if args.painel:
            panel = write_panel(repo, settings.database.parent)
        else:
            # A tarefa do Windows e a abertura manual podem chegar no mesmo minuto.
            # Um banco separado mantém a leitura das vagas livre durante a coleta.
            gate = settings.database.with_name(settings.database.name + ".lock")
            with closing(sqlite3.connect(gate, timeout=1800)) as lock:
                lock.execute("BEGIN EXCLUSIVE")
                if args.alarme:
                    br = ZoneInfo("America/Sao_Paulo")
                    now_br = datetime.now(br)
                    slot_key = f"alarme_{args.alarme}"
                    cp = repo.checkpoint(slot_key)
                    slot_done = bool(cp and cp.astimezone(br).date() == now_br.date())
                    if not slot_done:
                        cp_m = repo.checkpoint("alarme_manha")
                        morning_done = bool(cp_m and cp_m.astimezone(br).date() == now_br.date())
                        if args.alarme == "manha" or not morning_done:
                            y_mid = datetime.combine(now_br.date() - timedelta(days=1), dt_time(0, 0), tzinfo=br)
                            hours = max(26, min(72, math.ceil((now_br - y_mid).total_seconds() / 3600) + 2))
                        else:
                            t_mid = datetime.combine(now_br.date(), dt_time(0, 0), tzinfo=br)
                            hours = max(12, min(30, math.ceil((now_br - t_mid).total_seconds() / 3600) + 1))
                        code = asyncio.run(hunt(settings, repo, hours, False, args.skip_ai, console))
                        if code == 0:
                            repo.complete(slot_key, datetime.now(timezone.utc))
                else:
                    last = repo.last_hunt()
                    done_today = (last and last["sources_ok"] and
                                  datetime.fromisoformat(last["finished"]).astimezone().date() == datetime.now().date())
                    if not args.diaria or not done_today:
                        code = asyncio.run(hunt(settings, repo, hours, False, args.skip_ai, console))
                panel = write_panel(repo, settings.database.parent)
        console.print(Text(f"Painel local · {panel}"))
        if args.abrir:
            webbrowser.open(panel.resolve().as_uri())
        return code
    except (OSError, ValueError, sqlite3.Error, yaml.YAMLError) as error:
        console.print(Text(f"Falha: {error}", style="red"))
        return 1
    finally:
        if repo:
            repo.db.close()


if __name__ == "__main__":
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
