import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from src.collectors.base import BR, BaseCollector, content, log, names, parse_time, safe_error, schema_jobs
from src.core.forms import application_form, google_form_url
from src.core.models import JobPosting
from src.filters.rules import contains_any


def expired(value: str | None, now: datetime) -> bool:
    stamp, precision = parse_time(value)
    if stamp is None and isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value).replace(tzinfo=BR)
        except ValueError:
            return False
    return bool(stamp and (stamp + timedelta(days=precision == "day")) <= now)


class Greenhouse(BaseCollector):
    def queries(self):
        return self.ctx.settings.greenhouse_boards

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{query}/jobs"
        rows = (await self.get(endpoint)).json()["jobs"]
        relevant = [r for r in rows if r.get("id") and r.get("absolute_url")
                    and contains_any(r.get("title") or "", self.ctx.settings.roles)]
        relevant.sort(key=lambda r: r.get("first_published") or r.get("updated_at") or "", reverse=True)
        for row in relevant[:self.ctx.settings.results_per_search]:
            # Uma edição recente pode ser de uma vaga antiga. Só first_published prova a publicação.
            latest, _ = parse_time(row.get("first_published") or row.get("updated_at"))
            if latest and (latest < self.ctx.form_cutoff or
                           (latest < self.ctx.cutoff and not contains_any(row["title"], self.ctx.settings.positive))):
                continue
            try:
                detail = (await self.get(f"{endpoint}/{row['id']}")).json()
            except (httpx.HTTPError, ValueError) as error:
                self.errors += 1
                log.warning("greenhouse · %s/%s · %s", query, row["id"], safe_error(error))
                continue
            if expired(detail.get("application_deadline"), self.ctx.now):
                continue
            stamp, precision = parse_time(detail.get("first_published") or row.get("first_published"))
            yield JobPosting(source=self.name, title=row["title"], url=row["absolute_url"],
                             company=detail.get("company_name") or self.ctx.settings.greenhouse_boards[query],
                             location=(row.get("location") or {}).get("name") or "",
                             description=content(unescape(detail.get("content") or "")),
                             precision=precision, published_at=stamp)


class Ashby(BaseCollector):
    def queries(self):
        return self.ctx.settings.ashby_boards

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        response = await self.get(f"https://api.ashbyhq.com/posting-api/job-board/{query}")
        rows = [r for r in response.json()["jobs"] if r.get("isListed") is not False and r.get("jobUrl")
                and contains_any(r.get("title") or "", self.ctx.settings.roles)]
        rows.sort(key=lambda r: r.get("publishedAt") or "", reverse=True)
        for row in rows[:self.ctx.settings.results_per_search]:
            stamp, precision = parse_time(row.get("publishedAt"))
            locations = [row, *(row.get("secondaryLocations") or [])]
            places = [loc.get("location") for loc in locations if loc.get("location")]
            countries = [loc["address"]["addressCountry"] for loc in locations
                         if (loc.get("address") or {}).get("addressCountry")]
            mode = {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}.get(row.get("workplaceType"), "unknown")
            yield JobPosting(source=self.name, title=row["title"], company=self.ctx.settings.ashby_boards[query],
                             url=row["jobUrl"], location=" / ".join(places), applicant_regions=countries + places if countries else [],
                             workplace=mode, is_remote=mode == "remote", level=row.get("employmentType") or "",
                             description=content(row.get("descriptionHtml")) or row.get("descriptionPlain") or "",
                             published_at=stamp, precision=precision)


class Amazon(BaseCollector):
    def queries(self):
        return ["software development", "machine learning"]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        limit = self.ctx.settings.results_per_search
        for offset in range(0, limit, 25):
            size = min(25, limit - offset)
            response = await self.get("https://www.amazon.jobs/en/search.json", params={
                "base_query": query, "normalized_country_code[]": "BRA", "sort": "recent",
                "offset": offset, "result_limit": size})
            rows = response.json()["jobs"]
            for row in rows:
                if not row.get("job_path", "").startswith("/en/jobs/") or not row.get("title"):
                    continue
                try:
                    posted = datetime.strptime(row.get("posted_date") or "", "%B %d, %Y").replace(tzinfo=BR)
                except ValueError:
                    posted = None
                location = row.get("location") or row.get("normalized_location") or ""
                yield JobPosting(source=self.name, title=row["title"], company="Amazon", location=location,
                                 is_remote=bool(re.search(r"\bvirtual\b", location, re.I)),
                                 level="intern" if row.get("is_intern") is True else "",
                                 url=urljoin("https://www.amazon.jobs", row["job_path"]),
                                 description="\n".join((content(row.get("description")),
                                                        "Basic qualifications\n" + content(row.get("basic_qualifications")),
                                                        "Preferred qualifications\n" + content(row.get("preferred_qualifications")))),
                                 published_at=posted, precision="day" if posted else "unknown")
            if len(rows) < size:
                break


class Himalayas(BaseCollector):
    def queries(self):
        return ["software engineer", "developer"]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        limit = self.ctx.settings.results_per_search
        seen = set()
        for page in range(1, (limit + 19) // 20 + 1):
            response = await self.get("https://himalayas.app/jobs/api/search", params={
                "q": query, "country": "BR", "seniority": "Entry-level", "sort": "recent", "page": page})
            rows = response.json()["jobs"]
            if not rows:
                break
            for row in rows:
                url = row.get("applicationLink")
                if not url or url in seen or not row.get("title") or not row.get("pubDate"):
                    continue
                seen.add(url)
                if row.get("expiryDate") and row["expiryDate"] <= self.ctx.now.timestamp():
                    continue
                regions = row.get("locationRestrictions") or []
                form = google_form_url(url)
                yield JobPosting(source=self.name, title=row["title"], url=form or url, form_url=form, company=row.get("companyName") or "",
                                 location=", ".join(regions) or "Mundial", applicant_regions=regions,
                                 level=" ".join(row.get("seniority") or []), workplace="remote", is_remote=True,
                                 description=content(row.get("description")), precision="updated",
                                 updated_at=datetime.fromtimestamp(row["pubDate"], timezone.utc))
                if len(seen) >= limit:
                    return


class WeWorkRemotely(BaseCollector):
    def queries(self):
        return ["full-stack", "back-end", "front-end"]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        response = await self.get(f"https://weworkremotely.com/categories/remote-{query}-programming-jobs.rss")
        root = ElementTree.fromstring(response.content)
        if root.find("channel") is None:
            raise RuntimeError("Feed de vagas indisponível")
        for item in root.findall("./channel/item")[:self.ctx.settings.results_per_search]:
            url = item.findtext("link") or ""
            if not url.startswith("https://weworkremotely.com/remote-jobs/"):
                continue
            name = item.findtext("title") or ""
            company, separator, title = name.partition(": ")
            try:
                posted = parsedate_to_datetime(item.findtext("pubDate") or "")
            except (ValueError, TypeError):
                continue
            if not posted.tzinfo or posted < self.ctx.form_cutoff or not name:
                continue
            region = item.findtext("region") or ""
            yield JobPosting(source=self.name, title=title if separator else name, company=company if separator else "",
                             location=region, applicant_regions=[region] if region else [], is_remote=True,
                             description=content(item.findtext("description")), url=url, published_at=posted, precision="timestamp")


class ProgramaThor(BaseCollector):
    base_url = "https://programathor.com.br"

    def queries(self):
        return ["Júnior"]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        response = await self.get(f"{self.base_url}/jobs-city/remoto", params={"expertise": query})
        soup = BeautifulSoup(response.text, "html.parser")
        seen = set()
        candidates = []
        for anchor in soup.select("a[href]"):
            href = anchor["href"].split("?", 1)[0].split("#", 1)[0]
            if not re.fullmatch(r"/jobs/\d+-[^/]+", href) or href in seen:
                continue
            seen.add(href)
            label = anchor.get_text(" ", strip=True)
            if label and contains_any(label, self.ctx.settings.blocked):
                continue
            if len(label) > 20 and not contains_any(label, self.ctx.settings.roles):
                continue
            candidates.append(href)
        for href in candidates[:self.ctx.settings.results_per_search]:
            url = urljoin(self.base_url, href)
            try:
                detail = await self.get(url)
            except (httpx.HTTPError, ValueError) as error:
                self.errors += 1
                log.warning("programathor · %s · %s", href, safe_error(error))
                continue
            detail_soup = BeautifulSoup(detail.text, "html.parser")
            jobs = list(schema_jobs(detail_soup))
            if not jobs:
                continue
            item = jobs[0]
            if not item.get("title") or expired(item.get("validThrough"), self.ctx.now):
                continue
            description = content(item.get("description"))
            form = application_form(description) or application_form(content(detail.text))
            stamp, precision = parse_time(item.get("datePosted"))
            regions = names(item.get("applicantLocationRequirements"))
            yield JobPosting(
                source=self.name,
                title=item["title"],
                url=url,
                company=", ".join(names(item.get("hiringOrganization"))),
                location=", ".join(regions) or "Brasil",
                applicant_regions=regions,
                is_remote="TELECOMMUTE" in names(item.get("jobLocationType")),
                form_url=form,
                description=description,
                published_at=stamp,
                precision=precision,
            )
