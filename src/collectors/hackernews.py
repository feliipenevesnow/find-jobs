from collections.abc import AsyncIterator

from src.collectors.base import BaseCollector, content, parse_time
from src.core.models import JobPosting
from src.filters.rules import contains_any


class HackerNews(BaseCollector):
    endpoint = "https://hn.algolia.com/api/v1/search_by_date"

    def queries(self):
        return ["Who is hiring?"]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        stories = (await self.get(self.endpoint, params={
            "tags": "story,author_whoishiring", "query": query, "hitsPerPage": 5})).json()["hits"]
        for story in stories[:2]:
            story_id = story["objectID"]
            if not (story.get("title") or "").lower().startswith("ask hn: who is hiring?"):
                continue
            for term in ("forms.gle", "docs.google.com/forms", ""):
                cutoff = self.ctx.form_cutoff if term else self.ctx.cutoff
                limit = self.ctx.settings.results_per_search
                for offset in range(0, limit, 100):
                    rows = (await self.get(self.endpoint, params={
                        "tags": f"comment,story_{story_id}", "query": term, "numericFilters": f"created_at_i>={int(cutoff.timestamp())}",
                        "hitsPerPage": min(100, limit), "page": offset // 100})).json()["hits"]
                    for row in rows[:limit - offset]:
                        if str(row.get("parent_id")) != story_id or not row.get("comment_text"):
                            continue
                        description = content(row["comment_text"].replace("<p>", "\n<p>"))
                        header = description.splitlines()[0]
                        fields = [part.strip() for part in header.split("|")]
                        title = next((field for field in fields[1:] if contains_any(field, self.ctx.settings.roles)), "")
                        if not title and len(header) <= 200 and contains_any(header, self.ctx.settings.roles):
                            title = header
                        if not title:
                            continue
                        stamp, precision = parse_time(row.get("created_at"))
                        location = " / ".join(p for p in fields[1:] if "remote" in p.lower() or "onsite" in p.lower())
                        yield JobPosting(source=self.name, title=title, company=fields[0] if len(fields) > 1 else "",
                                         location=location, url="https://news.ycombinator.com/item?id=" + row["objectID"],
                                         published_at=stamp, precision=precision, description=description)
                    if len(rows) < min(100, limit):
                        break
