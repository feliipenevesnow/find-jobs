import asyncio
import json
import subprocess
import sys
from pathlib import Path
from collections.abc import AsyncIterator

from src.collectors.base import BaseCollector, parse_time
from src.core.forms import application_form, google_form_url
from src.core.models import JobPosting


class JobSpy(BaseCollector):
    def form_queries(self):
        role = self.ctx.settings.roles[0]
        return [f'"forms.gle" {role}', f'"docs.google.com/forms" {role}']

    def queries(self):
        return [*self.ctx.settings.search_terms, *self.form_queries()]

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        # Um processo por consulta permite interromper um portal travado sem perder o ciclo.
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        form_search = query in self.form_queries()
        hours = max(self.ctx.hours, self.ctx.settings.forms_lookback_days * 24) if form_search else self.ctx.hours
        rows = []
        for attempt in range(2):
            process = await asyncio.create_subprocess_exec(
                str(python), "-X", "utf8", "-m", "src.collectors.jobspy",
                self.name, query, str(hours), str(self.ctx.settings.results_per_search), str(int(self.ctx.settings.remote)),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                output, errors = await asyncio.wait_for(process.communicate(), timeout=100)
            except (TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.wait()
                raise
            if process.returncode == 0:
                rows = json.loads(output)
                if rows or b"ERROR" not in errors.upper():
                    break
            if attempt == 0:
                await asyncio.sleep(3.0)
            elif process.returncode:
                raise RuntimeError("JobSpy falhou ao consultar o portal")
            else:
                raise RuntimeError("JobSpy registrou erro na consulta")
        for row in rows:
            if not row.get("title") or not row.get("job_url"):
                continue
            stamp, precision = parse_time((row.get("date_posted") or "")[:10])
            description = row.get("description") or ""
            form = google_form_url(row.get("job_url_direct") or "") or application_form(description)
            if form_search and not form:
                continue
            if not form_search:
                precision = "native"
            elif not stamp:
                precision = "first_seen"
            salary = " ".join(str(x) for x in [row.get("currency"), row.get("min_amount"),
                                               row.get("max_amount"), row.get("interval")] if x)
            yield JobPosting(
                title=row["title"], company=row.get("company") or "", location=row.get("location") or "",
                is_remote=bool(row.get("is_remote")), url=row["job_url"], source=self.name,
                published_at=stamp, precision=precision, salary=salary, level=row.get("job_level") or "",
                form_url=form, description=description,
            )
        await asyncio.sleep(1.5 if self.name == "linkedin" else 0.5)


if __name__ == "__main__":
    from jobspy import scrape_jobs

    site, term, hours, count, remote = sys.argv[1:]
    frame = scrape_jobs(site_name=site, search_term=term, location="Brazil", country_indeed="Brazil",
                        hours_old=int(hours), results_wanted=int(count), is_remote=remote == "1", fetch_description=True)
    print(frame.to_json(orient="records", date_format="iso", force_ascii=True))
