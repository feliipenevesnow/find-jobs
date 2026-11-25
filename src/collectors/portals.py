from collections.abc import AsyncIterator
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from src.collectors import monitor_parsers as parsers
from src.collectors.base import BaseCollector, content, log, parse_time, safe_error
from src.core.models import JobPosting, normalized
from src.filters.rules import contains_any


class HtmlBoard(BaseCollector):
    def queries(self):
        for term in self.ctx.settings.search_terms:
            if self.name == "infojobs":
                yield term.strip()
            else:
                yield normalized(term).replace(" ", "-")

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        if self.name == "infojobs":
            response = await self.get(
                f"{parsers.INFOJOBS_BASE}/empregos.aspx",
                params={"palabra": query, "campo": "griddate", "orden": "desc"},
            )
            soup = BeautifulSoup(response.text, "html.parser")
            rows = parsers._extrair_jsonld(soup) or parsers._extrair_cards(soup, "Brasil")
        else:
            response = await self.get(f"{parsers.VAGASCOM_BASE}/vagas-de-{query}", params={"ordenar_por": "mais_recentes"})
            rows = parsers._vc_extrair_cards(response.text)
        for row in rows[:self.ctx.settings.results_per_search]:
            stamp, precision = parse_time(row.get("date_posted"))
            yield JobPosting(source=self.name, title=row["title"], url=row["job_url"],
                             company=row["company"], location=row["location"], level=row.get("level", ""),
                             is_remote=row.get("remote", False) or contains_any(row["location"] + " " + row["title"], ["remoto", "home office"]),
                             description=row["description"], published_at=stamp, precision=precision)


class Nube(BaseCollector):
    sitemap = "https://www.nube.com.br/sitemap-vagas.xml"
    detail = "https://www.nube.com.br/api/portal/buscar_vaga_encaminhamento"

    def queries(self):
        return ["sitemap"]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        root = ElementTree.fromstring((await self.get(self.sitemap)).content)
        links = root.findall(".//{*}loc")
        if not links:
            raise RuntimeError("O sitemap do Nube veio sem links")
        for element in links:
            url = (element.text or "").strip()
            match = parsers._NUBE_URL.search(url)
            if not match or urlsplit(url).hostname != "www.nube.com.br":
                continue
            code, kind, slug = match.groups()
            suffix = parsers._NUBE_CAUDA.match(slug)
            if not suffix or url in self.ctx.known_nube:
                continue
            label = suffix["meio"].replace("-", " ")
            if not contains_any(label, self.ctx.settings.roles):
                continue
            title = ("Estágio" if kind.lower() == "estagio" else "Aprendiz") + " em " + label
            description = ""
            if self.ctx.last_nube and self.ctx.last_nube >= self.ctx.cutoff:
                # A primeira leitura serve só de inventário. Depois detalhamos as inclusões.
                try:
                    response = await self.get(self.detail, params={"id_vaga": code, "titulo": slug})
                    fields = response.json()["param"]["requisito"]["linhas"]
                    if not isinstance(fields, dict):
                        raise ValueError("Formato inesperado no detalhe")
                except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
                    self.errors += 1
                    log.warning("nube · detalhe %s indisponível · %s", code, safe_error(error))
                    continue
                lines = []
                for key, value in fields.items():
                    if isinstance(value, dict) and value.get("mostrar", True):
                        text = value.get("valor") or value.get("texto") or value.get("lista")
                        if isinstance(text, (str, list)):
                            description_text = " ".join(str(v) for v in text) if isinstance(text, list) else text
                            lines.append(f"{key} {content(description_text)}")
                description = " ".join(lines)
            yield JobPosting(source=self.name, title=title, url=url, precision="discovery",
                             location=f"{label}, {suffix['uf'].upper()}", salary="R$ " + suffix["valor"],
                             description=description)
