import re
from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, content, parse_time
from src.core.forms import application_form
from src.core.models import JobPosting
from src.filters.rules import contains_any


def announcement_title(lines: list[str], roles: list[str]) -> str:
    for line in lines[:8]:
        cleaned = re.sub(r"^[\s#*\-:📌🚀💻📜🎯🔥💼📣✨⚡👉🔹▪️📋]+", "", line).strip()
        cleaned = re.sub(r"(?i)^(?:t[íi]tulo|cargo|vaga|posi[çc][ãa]o|oportunidade|descri[çc][ãa]o)\s*:\s*", "", cleaned).strip()
        stripped_tags = re.sub(r"#[\w-]+", "", cleaned).strip()
        if len(stripped_tags) < 3:
            continue
        title = re.sub(r"#([\w-]+)", r"\1", cleaned).strip(" \t*-:")
        if len(title) > 200 or not contains_any(title, roles):
            continue
        if re.match(r"(?i)^(?:requisitos?|experiência|conhecimento|responsabilidades?|benefícios?|diferenciais|sobre)\b", title):
            continue
        return title
    return ""


def structured_field(lines: list[str], names: str) -> str:
    for line in lines[:12]:
        if match := re.match(rf"(?i)^[^\w#]*(?:{names})\s*:\s*(.+)$", line.strip()):
            return match[1].strip(" \t*-:")
    return ""


class Communities(BaseCollector):
    def queries(self):
        return [f"{channel}:{term}" for channel in self.ctx.settings.community_channels
                for term in ("", "forms.gle", "docs.google.com/forms")]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        channel, term = query.split(":", 1)
        seen = set()
        params = {"q": term} if term else {}
        limit = self.ctx.settings.results_per_search
        while len(seen) < limit:
            response = await self.get(f"https://t.me/s/{channel}", params=params)
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select(".tgme_widget_message[data-post]")
            if not cards and not params and not soup.select_one(".tgme_channel_info"):
                raise RuntimeError("Prévia pública do canal indisponível")
            previous = len(seen)
            oldest = None
            for card in reversed(cards):
                match = re.fullmatch(re.escape(channel) + r"/(\d+)", card["data-post"], re.I)
                if not match or match[1] in seen:
                    continue
                seen.add(match[1])
                oldest = match[1]
                date = card.select_one("time[datetime]")
                body = card.select_one(".tgme_widget_message_text")
                if not date or not body:
                    continue
                stamp, precision = parse_time(date["datetime"])
                if stamp and stamp < (self.ctx.form_cutoff if term else self.ctx.cutoff):
                    return
                description = content(str(body))
                lines = [line.strip() for line in description.splitlines() if line.strip()]
                form = application_form(description)
                title = announcement_title(lines, self.ctx.settings.roles)
                if not title and form and lines:
                    title = lines[0][:200]
                if title:
                    tags = " ".join(re.findall(r"#([\w-]+)", description))
                    yield JobPosting(
                        source=self.name,
                        title=title,
                        company=structured_field(lines, "empresa|company"),
                        location=structured_field(lines, r"local(?:iza[çc][ãa]o)?|location|cidade"),
                        url=f"https://t.me/{channel}/{match[1]}",
                        level=tags,
                        description=description,
                        form_url=form,
                        published_at=stamp,
                        precision=precision,
                    )
                if len(seen) >= limit:
                    return
            if not oldest or len(seen) == previous:
                break
            params["before"] = oldest
