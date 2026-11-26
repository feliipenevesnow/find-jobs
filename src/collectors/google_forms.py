"""Inspeciona o que o Google Forms publica para quem abre o link de resposta."""
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from src.collectors.base import BR, meta
from src.collectors.community import announcement_title
from src.collectors.public_page import public_page
from src.core.forms import google_form_url
from src.core.models import FormDetails, FormStatus, JobPosting, plain
from src.filters.rules import contains_any

HIRING = re.compile(r"\b(vagas?|candidat\w*|contrata\w*|oportunidade|inscri\w*|processo seletivo|"
                    r"estamos (?:em busca de|buscando|procurando)|buscamos (?:um|uma|pessoa)|"
                    r"integrar noss[oa] (?:time|equipe)|hiring|application|apply|job opening|join our team)\b")
NON_JOB = re.compile(r"\b(pesquisa(?! e desenvolvimento)|survey|workshop|curso (?:de|para)|evento|palestra|newsletter|"
                     r"banco de talentos|talent pool|questionario(?! (?:de |do |para )?(?:candidatura|processo seletivo|recrutamento))|"
                     r"encuesta|mentoria|briefing)\b")
ROLE_FIELD = re.compile(r"^(?:cargo|job title|titulo da vaga)\s*:\s*(.+)$", re.M)
CLOSED = re.compile(r"nao (?:esta |esta mais |esta mais a |aceita mais |esta aceitando mais |e mais )?(?:aceitando |a aceitar |recebendo |aceit[ao]s? )?(?:mais )?respostas|no longer (?:accepting|taking) responses|inscricoes encerradas")


@dataclass(frozen=True)
class FormInspection:
    url: str
    status: FormStatus
    details: FormDetails | None = None
    job_opening: bool = False

    def posting(self) -> JobPosting | None:
        if not self.job_opening or self.status != "open" or not self.details:
            return None
        return JobPosting(source="web", url=self.url, title=self.details.title, description=self.details.description,
                          form_url=self.url, form_status=self.status, form_details=self.details, precision="first_seen")


def form_state(soup: BeautifulSoup) -> FormStatus:
    text = plain(soup.get_text(" ", strip=True))
    if CLOSED.search(text):
        return "closed"
    deadline = re.search(r"\b(?:inscricoes|candidaturas|prazo)(?: abertas?)?\s*(?:ate|:)\s*(\d{2}/\d{2}/\d{4})\b", text)
    if deadline:
        try:
            if datetime.strptime(deadline[1], "%d/%m/%Y").date() < datetime.now(BR).date():
                return "closed"
        except ValueError:
            pass
    return "open" if soup.select_one('form[action*="/formResponse"]') else "listed"


def inspect_page(url: str, soup: BeautifulSoup, roles: list[str]) -> FormInspection:
    status = form_state(soup)
    if status != "open":
        return FormInspection(url, status)
    heading = soup.select_one('[role="heading"][aria-level="1"], h1, .freebirdFormviewerViewHeaderTitle') or soup.title
    title = meta(soup, "og:title") or (heading.get_text(" ", strip=True) if heading else "")
    title = re.sub(r"\s*[-–]\s*Google (?:Forms|Formulários)$", "", title, flags=re.I).strip()
    intro = soup.select_one(".freebirdFormviewerViewHeaderDescription, .cBGGJ")
    description = (intro.get_text("\n", strip=True) if intro else "") or meta(soup, "og:description")
    if NON_JOB.search(plain(title)) or NON_JOB.match(plain(description).lstrip()):
        return FormInspection(url, "unrelated")

    # As respostas oferecidas ao candidato e perguntas sobre preferências não descrevem o cargo.
    if title.endswith("?"):
        title = ""
    description = "\n".join(part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+", description)
                            if part.strip() and not part.rstrip().endswith("?"))
    header = (title + "\n" + description).strip()
    if (declared := ROLE_FIELD.search(plain(header))) and not contains_any(declared[1], roles):
        return FormInspection(url, "unrelated", FormDetails(title=title, description=header))
    lines = description.splitlines()
    role = title if contains_any(title, roles) else announcement_title(lines, roles)
    if not role and len(lines) > 8:
        role = announcement_title(lines[8:], roles)
    recruitment_field = re.search(r"\b(?:curriculo|pretensao salarial|cover letter|resume)\b",
                                  plain(soup.get_text(" ", strip=True)))
    hiring = bool(HIRING.search(plain(header)) or recruitment_field)
    label = f"{title} — {role}" if title and role and role != title else (role or title)
    details = FormDetails(title=label, description=header)
    return FormInspection(url, "open", details, job_opening=bool(role and hiring))


async def read_form(client: httpx.AsyncClient, url: str, roles: list[str]) -> FormInspection:
    link = google_form_url(url)
    if not link:
        raise ValueError("Use um link de resposta do Google Forms em forms.gle ou docs.google.com/forms.")
    try:
        final, soup = await public_page(client, link)
        dest = urlsplit(final)
        if dest.hostname == "docs.google.com" and dest.path.rstrip("/").endswith("/closedform"):
            return FormInspection(link, "closed")
        if dest.hostname == "docs.google.com" and (canonical := google_form_url(final)):
            return inspect_page(canonical, soup, roles)
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (404, 410):
            return FormInspection(link, "closed")
    except (ValueError, OSError, httpx.HTTPError):
        pass
    return FormInspection(link, "listed")
