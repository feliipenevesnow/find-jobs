import os
import re
from collections.abc import AsyncIterator

from src.collectors.base import BaseCollector, parse_time
from src.core.models import JobPosting, plain


class Github(BaseCollector):
    def queries(self):
        return self.ctx.settings.github_repos

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        headers = {"Accept": "application/vnd.github+json"}
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + token
        limit = self.ctx.settings.results_per_search
        size = min(100, limit)
        for offset in range(0, limit, size):
            response = await self.get(
                f"https://api.github.com/repos/{query}/issues", headers=headers,
                params={"sort": "created", "direction": "desc", "state": "open",
                        "per_page": size, "page": offset // size + 1},
            )
            issues = response.json()
            for row in issues[:limit-offset]:
                stamp, precision = parse_time(row.get("created_at"))
                if stamp and stamp < self.ctx.cutoff:
                    return
                if "pull_request" in row:
                    continue
                title = row["title"]
                labels = " ".join(i["name"] for i in row.get("labels", []) if isinstance(i, dict))
                body = row.get("body") or ""
                place = re.match(r"^\[([^\]]+)\]", title)
                company = re.search(r"\s(?:na|no|at|@)\s+(.+?)\s*$", title)
                yield JobPosting(title=title, url=row["html_url"], source=self.name,
                                 company=company[1] if company else "", description=body,
                                 location=place[1] if place else "", level=labels,
                                 is_remote=bool(re.search(r"\b(remot[oa]|remote|home office)\b", plain(title+" "+labels))),
                                 published_at=stamp, precision=precision)
            if len(issues) < size:
                break
