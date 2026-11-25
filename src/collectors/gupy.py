import json
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, content, parse_time, safe_error, log
from src.core.models import JobPosting
from src.filters.rules import contains_any


class Gupy(BaseCollector):
    endpoint = "https://portal.gupy.io/api/job-search/jobs"

    def queries(self):
        return self.ctx.settings.search_terms

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        limit = self.ctx.settings.results_per_search
        for offset in range(0, limit, 50):
            params = {"jobName": query, "offset": offset, "limit": min(50, limit - offset),
                      "sortBy": "publishedDate", "sortOrder": "desc"}
            response = await self.get(self.endpoint, params=params)
            rows = response.json()["data"]
            if not isinstance(rows, list):
                raise RuntimeError("Formato inesperado na listagem da Gupy")
            for row in rows:
                if not row.get("name") or not row.get("jobUrl"):
                    continue
                url = row["jobUrl"]
                host = urlsplit(url).hostname or ""
                if not host.endswith(".gupy.io") or urlsplit(url).scheme != "https":
                    continue
                stamp, precision = parse_time(row.get("publishedDate"))
                if row.get("type") == "vacancy_type_talent_pool":
                    continue
                job = JobPosting(
                    source=self.name, title=row["name"], company=row.get("careerPageName") or "",
                    location=", ".join(filter(None, [row.get("city"), row.get("state")])),
                    is_remote=row.get("workplaceType") == "remote",
                    url=url, published_at=stamp, precision=precision, description=content(row.get("description")),
                )
                if stamp and stamp >= self.ctx.cutoff and contains_any(job.title, self.ctx.settings.roles):
                    try:
                        detail = await self.get(url)
                        script = BeautifulSoup(detail.text, "html.parser").find("script", id="__NEXT_DATA__")
                        info = json.loads(script.get_text())["props"]["pageProps"]["job"] if script else {}
                        description = " ".join(content(info.get(k)) for k in
                                               ["description", "responsibilities", "prerequisites", "relevantExperiences"])
                        if description.strip():
                            job.description = description.strip()
                        job.location = ", ".join(filter(None, [info.get("addressCity"), info.get("addressState")])) or job.location
                    except Exception as error:
                        log.warning("gupy · detalhe indisponível · %s", safe_error(error))
                yield job
            if len(rows) < params["limit"]:
                break
