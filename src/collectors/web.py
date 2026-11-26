import os
import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from src.collectors.base import BR, BaseCollector, content, log, names, parse_time, safe_error, schema_jobs
from src.collectors.careers import expired
from src.collectors.community import announcement_title
from src.collectors.public_page import public_page
from src.core.forms import application_form, google_form_url
from src.core.models import JobPosting, canonical_url, plain
from src.filters.rules import contains_any


def meta(soup, key: str) -> str:
    element = soup.find("meta", property=key) or soup.find("meta", attrs={"name": key})
    return element.get("content", "").strip() if element else ""


HIRING = re.compile(r"\b(vagas?|candidat\w*|contrata\w*|oportunidade|inscri\w*|processo seletivo|hiring|application|apply|job opening)\b")
NON_JOB = re.compile(r"\b(pesquisa|survey|workshop|curso de|evento|palestra|newsletter|banco de talentos|talent pool|questionario|encuesta|mentoria|briefing)\b")
CLOSED = re.compile(r"nao (?:esta |esta mais |esta mais a |aceita mais |esta aceitando mais |e mais )?(?:aceitando |a aceitar |recebendo |aceit[ao]s? )?(?:mais )?respostas|no longer (?:accepting|taking) responses|inscricoes encerradas")
SKIP_HOSTS = {
    "wikipedia.org", "pt.wikipedia.org", "en.wikipedia.org", "en.m.wikipedia.org",
    "youtube.com", "www.youtube.com", "music.youtube.com",
    "dicio.com.br", "www.dicio.com.br", "significados.com.br", "www.significados.com.br",
    "querobolsa.com.br", "www.alura.com.br", "alura.com.br", "desenvolvedor.io",
    "horadecodar.com.br", "www.orientacarreira.com.br", "dictionary.cambridge.org",
    "forms.office.com", "forms.cloud.microsoft", "workspace.google.com", "developer.microsoft.com",
}


def form_state(soup: BeautifulSoup) -> str:
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
    if soup.select_one('form[action*="/formResponse"]'):
        return "open"
    return "listed"


def form_posting(url: str, soup: BeautifulSoup, roles: list[str]) -> JobPosting | None:
    form = google_form_url(url)
    if not form or form_state(soup) != "open":
        return None
    heading = soup.select_one('[role="heading"][aria-level="1"], h1, .freebirdFormviewerViewHeaderTitle')
    title = meta(soup, "og:title") or (heading.get_text(" ", strip=True) if heading else "")
    title = re.sub(r"\s*[-–]\s*Google (?:Forms|Formulários)$", "", title, flags=re.I)
    intro = soup.select_one(".freebirdFormviewerViewHeaderDescription, .cBGGJ")
    intro_dom = intro.get_text("\n", strip=True) if intro else ""
    intro_meta = meta(soup, "og:description")
    description = intro_meta if len(intro_meta) > len(intro_dom) else intro_dom
    # Perguntas sobre preferência por trabalho remoto não descrevem a modalidade da vaga.
    header = title + "\n" + description
    recruitment_field = re.search(r"\b(?:curriculo|pretensao salarial|cover letter|resume)\b", plain(soup.get_text(" ", strip=True)))
    if not (HIRING.search(plain(header)) or recruitment_field) or NON_JOB.search(plain(title)):
        return None
    role = title if contains_any(title, roles) else announcement_title(description.splitlines(), roles)
    if not role:
        for clause in re.split(r"[.!?\n]+", description):
            candidate = clause.strip(" \t#*-:📌🚀💻📜🎯")
            if not candidate or len(candidate) > 180 or not contains_any(candidate, roles):
                continue
            if re.match(r"(?i)^(?:requisitos?|experiência|conhecimento|responsabilidades?|benefícios?|diferenciais|sobre)\b", candidate):
                continue
            if 1 <= len(title) <= 60 and len(f"{title} — {candidate}") <= 180:
                role = f"{title} — {candidate}"
            else:
                role = candidate
            break
    if not role:
        return None
    return JobPosting(source="web", title=role, description=header, url=form, form_url=form,
                      form_status="open", precision="first_seen")


def web_posting(url, soup, ctx) -> JobPosting | None:
    if google_form_url(url):
        return form_posting(url, soup, ctx.settings.roles)
    jobs = list(schema_jobs(soup))
    container = soup.select_one("article") or soup.select_one("main") or soup.body
    if len(jobs) == 1:
        item = jobs[0]
        if not item.get("title") or expired(item.get("validThrough"), ctx.now):
            return None
        description = content(item.get("description"))
        regions = names(item.get("applicantLocationRequirements"))
        places = item.get("jobLocation") or []
        locations = []
        for place in places if isinstance(places, list) else [places]:
            address = place.get("address") if isinstance(place, dict) else None
            if isinstance(address, dict):
                locations.extend(names(address.get("addressLocality")) + names(address.get("addressCountry")))
        stamp, precision = parse_time(item.get("datePosted"))
        form = application_form(description)
        if not form and container and ("forms.gle/" in str(container) or "docs.google.com/forms/" in str(container)):
            form = application_form(content(str(container)))
        return JobPosting(source="web", url=url, title=item["title"], description=description,
                          company=", ".join(names(item.get("hiringOrganization"))), location=", ".join(regions or locations),
                          applicant_regions=regions, is_remote="TELECOMMUTE" in names(item.get("jobLocationType")),
                          form_url=form, published_at=stamp, precision="first_seen" if form and not stamp else precision)
    if jobs or not container:
        return None
    # Sem dados estruturados, exige um anúncio individual com uma ação de candidatura.
    headings = container.select("h1") or soup.select("h1")
    if len(headings) != 1:
        return None
    title = headings[0].get_text(" ", strip=True)
    if not contains_any(title, ctx.settings.roles) or NON_JOB.search(plain(title)):
        return None
    for unrelated in container.select("nav, footer, aside, script, style, form"):
        unrelated.decompose()
    description = content(str(container))
    form = application_form(description)
    date = container.select_one("time[datetime][itemprop=datePublished], time.published[datetime]")
    stamp, precision = parse_time(meta(soup, "article:published_time") or (date["datetime"] if date else None))
    if not form:
        apply_label = re.compile(r"\b(?:candidate-se|candidatar|inscreva-se|apply now|apply for (?:this|the) (?:job|position))\b")
        if not stamp or not any(apply_label.search(plain(a.get_text(" ", strip=True))) for a in container.select("a[href]")):
            return None
    return JobPosting(source="web", title=title, url=url, description=description, form_url=form,
                      published_at=stamp, precision=precision if stamp else "first_seen")


class WebSearch(BaseCollector):
    def __init__(self, name, ctx):
        super().__init__(name, ctx)
        self.seen = set()
        self.use_bing = False

    def queries(self):
        return self.ctx.settings.web_queries

    async def results(self, query):
        if key := os.getenv("BRAVE_SEARCH_API_KEY", "").strip():
            response = await self.get("https://api.search.brave.com/res/v1/web/search", follow_redirects=False,
                                      headers={"X-Subscription-Token": key},
                                      params={"q": query, "country": "BR", "count": 20})
            return [(i["url"], i.get("title", "") + " " + i.get("description", ""))
                    for i in response.json().get("web", {}).get("results", []) if i.get("url")]
        if not self.use_bing:
            try:
                response = await self.get(
                    "https://br.search.yahoo.com/search",
                    params={"p": query, "ei": "UTF-8"},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                soup = BeautifulSoup(response.content, "html.parser")
                if soup.select_one("form#challenge"):
                    raise RuntimeError("Índice HTML indisponível")
                results = []
                for card in soup.select("div.algo, div.Sr, .dd.algo"):
                    link = card.select_one("a[href]")
                    if not link:
                        continue
                    href = link["href"]
                    match = re.search(r"/RU=([^/]+)/", href)
                    target = unquote(match[1]) if match else href
                    host = (urlsplit(target).hostname or "").lower()
                    if target.startswith("http") and host and not (host == "yahoo.com" or host.endswith(".yahoo.com")):
                        results.append((target, card.get_text(" ", strip=True)))
                no_results = bool(soup.select_one(".compText, .no-results")) or "nao encontramos resultados" in plain(soup.get_text(" "))
                if results or no_results:
                    return results
                raise RuntimeError("Índice HTML indisponível")
            except (httpx.HTTPError, RuntimeError) as error:
                self.errors += 1
                if not (isinstance(error, httpx.HTTPStatusError) and error.response.status_code >= 500):
                    self.use_bing = True
                log.warning("web · Yahoo · %s. Consultando o índice RSS do Bing.", safe_error(error))
        response = await self.get("https://www.bing.com/search", params={
            "q": query, "format": "rss", "setlang": "pt-br", "cc": "br"})
        root = ElementTree.fromstring(response.content)
        if root.find("channel") is None:
            raise RuntimeError("Índice RSS indisponível")
        return [(i.findtext("link") or "", (i.findtext("title") or "") + " " + (i.findtext("description") or ""))
                for i in root.findall("./channel/item")]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        inspected = 0
        for target, snippet in await self.results(query):
            try:
                url = canonical_url(target)
            except ValueError:
                continue
            if url in self.seen or not url.startswith("https://"):
                continue
            host = (urlsplit(url).hostname or "").lower()
            if host in SKIP_HOSTS or (host == "docs.google.com" and not google_form_url(url)):
                continue
            if not google_form_url(url) and not contains_any(snippet, self.ctx.settings.roles):
                continue
            self.seen.add(url)
            inspected += 1
            try:
                final, soup = await public_page(self.ctx.client, url)
                if job := web_posting(final, soup, self.ctx):
                    yield job
            except (ValueError, OSError, httpx.HTTPError) as error:
                self.errors += 1
                log.warning("web · %s · %s", urlsplit(url).hostname, safe_error(error))
            if inspected >= min(self.ctx.settings.web_pages_per_query, self.ctx.settings.results_per_search):
                break
