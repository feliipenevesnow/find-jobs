import asyncio
import json
import subprocess
import sys
from pathlib import Path
from collections.abc import AsyncIterator

from src.collectors.base import BaseCollector, parse_time
from src.core.models import JobPosting


class JobSpy(BaseCollector):
    def queries(self):
        return self.ctx.settings.search_terms

    async def fetch(self, query: str) -> AsyncIterator[JobPosting]:
        # Um processo por consulta permite interromper um portal travado sem perder o ciclo.
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        process = await asyncio.create_subprocess_exec(
            str(python), "-X", "utf8", "-m", "src.collectors.jobspy",
            self.name, query, str(self.ctx.hours), str(self.ctx.settings.results_per_search),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            output, errors = await asyncio.wait_for(process.communicate(), timeout=100)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError("JobSpy falhou ao consultar o portal")
        rows = json.loads(output)
        if not rows and b"ERROR" in errors.upper():
            raise RuntimeError("JobSpy registrou erro na consulta")
        for row in rows:
            if not row.get("title") or not row.get("job_url"):
                continue
            stamp, _ = parse_time((row.get("date_posted") or "")[:10])
            salary = " ".join(str(x) for x in [row.get("currency"), row.get("min_amount"),
                                               row.get("max_amount"), row.get("interval")] if x)
            yield JobPosting(
                title=row["title"], company=row.get("company") or "", location=row.get("location") or "",
                is_remote=bool(row.get("is_remote")), url=row["job_url"], source=self.name,
                published_at=stamp, precision="native", salary=salary, level=row.get("job_level") or "",
                description=row.get("description") or "",
            )
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    from jobspy import scrape_jobs

    site, term, hours, count = sys.argv[1:]
    frame = scrape_jobs(site_name=site, search_term=term, location="Brazil", country_indeed="Brazil",
                        hours_old=int(hours), results_wanted=int(count), fetch_description=True)
    print(frame.to_json(orient="records", date_format="iso", force_ascii=True))
