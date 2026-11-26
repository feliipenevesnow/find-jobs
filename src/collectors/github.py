import os
import re
from collections.abc import AsyncIterator

from src.collectors.base import BaseCollector, parse_time
from src.core.forms import application_form
from src.core.models import JobPosting, plain


class Github(BaseCollector):
    def queries(self):
        return [*self.ctx.settings.github_repos, "forms.gle", "docs.google.com/forms"]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        headers = {"Accept": "application/vnd.github+json"}
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + token
        limit = self.ctx.settings.results_per_search
        size = min(100, limit)
        form_search = query in ("forms.gle", "docs.google.com/forms")
        for offset in range(0, limit, size):
            params = {"sort": "created", "per_page": size, "page": offset // size + 1}
            if form_search:
                params.update(q=f'is:issue is:open "{query}" vaga in:body,title created:>={self.ctx.form_cutoff:%Y-%m-%d}', order="desc")
                response = await self.get("https://api.github.com/search/issues", headers=headers, params=params)
                issues = response.json()["items"]
            else:
                params.update(direction="desc", state="open")
                response = await self.get(f"https://api.github.com/repos/{query}/issues", headers=headers, params=params)
                issues = response.json()
            for row in issues[:limit-offset]:
                stamp, precision = parse_time(row.get("created_at"))
                if stamp and stamp < self.ctx.form_cutoff:
                    return
                if "pull_request" in row:
                    continue
                title = row["title"]
                labels = " ".join(i["name"] for i in row.get("labels", []) if isinstance(i, dict))
                body = row.get("body") or ""
                form = application_form(body)
                if (form_search or (stamp and stamp < self.ctx.cutoff)) and not form:
                    continue
                place = re.match(r"^\[([^\]]+)\]", title)
                company = re.search(r"\s(?:na|no|at|@)\s+(.+?)\s*$", title)
                yield JobPosting(title=title, url=row["html_url"], source=self.name,
                                 company=company[1] if company else "", description=body,
                                 location=place[1] if place else "", level=labels,
                                 is_remote=bool(
                                     place
                                     and re.search(r"\b(?:remot[oa]|remote|home[ -]?office)\b", plain(place[1]))
                                     and not re.search(r"hibrid|hybrid|presencial|onsite", plain(place[1]))
                                 ),
                                 published_at=stamp, precision=precision, form_url=form)
            if len(issues) < size:
                break
