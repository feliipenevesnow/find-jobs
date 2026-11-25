import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import AiBudget
from src.core.models import Analysis, Decision, JobPosting, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE,
    posting TEXT NOT NULL,
    decision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'nova',
    collected_at TEXT NOT NULL,
    eligible_at TEXT,
    delivery TEXT NOT NULL DEFAULT 'filtered',
    notified_at TEXT
);
CREATE TABLE IF NOT EXISTS aliases (
    alias TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS aliases_by_job ON aliases(job_id);
CREATE TABLE IF NOT EXISTS checkpoints (source TEXT PRIMARY KEY, completed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hunts (
    id INTEGER PRIMARY KEY, finished TEXT NOT NULL, hours INTEGER NOT NULL,
    listings INTEGER NOT NULL, sources_ok INTEGER NOT NULL, sources_total INTEGER NOT NULL, errors INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, started REAL NOT NULL,
    tokens INTEGER NOT NULL, outcome TEXT NOT NULL DEFAULT 'reserved', analysis TEXT
);
CREATE INDEX IF NOT EXISTS ai_calls_fingerprint ON ai_calls(fingerprint, started);
CREATE INDEX IF NOT EXISTS ai_calls_started ON ai_calls(started);
CREATE TABLE IF NOT EXISTS ai_cooldown (provider TEXT PRIMARY KEY, until REAL NOT NULL);
"""


class Repository:
    def __init__(self, path: Path, preview: bool = False):
        if preview:
            self.db = sqlite3.connect(":memory:")
            if path.is_file():
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                    source.backup(self.db)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._migrate_unique_url()

    def _migrate_unique_url(self) -> None:
        cols = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)").fetchall()}
        with self.db:
            if "url" not in cols:
                self.db.execute("ALTER TABLE jobs ADD COLUMN url TEXT")
            missing = self.db.execute("SELECT id, posting FROM jobs WHERE url IS NULL").fetchall()
            for row in missing:
                url = JobPosting.model_validate_json(row["posting"]).url
                owner = self.db.execute("SELECT id FROM jobs WHERE url=? AND id!=?", (url, row["id"])).fetchone()
                if owner:
                    self.db.execute("UPDATE OR IGNORE aliases SET job_id=? WHERE job_id=?", (owner["id"], row["id"]))
                    self.db.execute("DELETE FROM aliases WHERE job_id=?", (row["id"],))
                    self.db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
                else:
                    self.db.execute("UPDATE jobs SET url=? WHERE id=?", (url, row["id"]))
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_url_unique ON jobs(url)")

    def reserve_ai(self, fingerprint: str, provider: str, tokens: int, limits: AiBudget, preview: bool):
        """Reserva antes do HTTP. Outra execução vê a reserva mesmo após uma queda."""
        now = time.time()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            cached = self.db.execute("SELECT analysis FROM ai_calls WHERE fingerprint=? AND analysis IS NOT NULL LIMIT 1",
                                     (fingerprint,)).fetchone()
            if cached:
                return "cache", Analysis.model_validate_json(cached[0])
            if preview:
                return "preview", None
            cooldown = self.db.execute("SELECT until FROM ai_cooldown WHERE provider=?", (provider,)).fetchone()
            if cooldown and cooldown[0] > now:
                return "cooldown", None
            recent = self.db.execute("SELECT outcome FROM ai_calls WHERE fingerprint=? AND started>? ORDER BY id DESC LIMIT 1",
                                     (fingerprint, now - 120)).fetchone()
            if recent:
                return "busy" if recent[0] == "reserved" else "recent_failure", None
            count, used = self.db.execute("SELECT count(*), coalesce(sum(tokens),0) FROM ai_calls WHERE started>?",
                                          (now - 86400,)).fetchone()
            if count >= limits.requests_per_24h or used + tokens > limits.tokens_per_24h or tokens > limits.tokens_per_minute:
                return "budget", None
            minute, oldest = self.db.execute("SELECT coalesce(sum(tokens),0), min(started) FROM ai_calls WHERE started>?",
                                             (now - 60,)).fetchone()
            if minute + tokens > limits.tokens_per_minute:
                return "wait", max(0.1, oldest + 60 - now)
            call = self.db.execute("INSERT INTO ai_calls(fingerprint,started,tokens) VALUES(?,?,?)",
                                   (fingerprint, now, tokens))
            return "reserved", call.lastrowid

    def finish_ai(self, call_id: int, analysis: Analysis | None, tokens: int | None):
        with self.db:
            self.db.execute("UPDATE ai_calls SET outcome=?, analysis=?, tokens=coalesce(?,tokens) WHERE id=?",
                            ("ok" if analysis else "failed", analysis.model_dump_json() if analysis else None,
                             tokens, call_id))

    def pause_ai(self, provider: str, seconds: float):
        with self.db:
            self.db.execute("""INSERT INTO ai_cooldown VALUES(?,?) ON CONFLICT(provider)
                               DO UPDATE SET until=max(until,excluded.until)""", (provider, time.time() + seconds))

    def ai_usage(self):
        return self.db.execute("SELECT count(*), coalesce(sum(tokens),0) FROM ai_calls WHERE started>?",
                               (time.time() - 86400,)).fetchone()

    def lookup(self, job: JobPosting):
        keys = ("url:" + job.url, "id:" + job.id_hash)
        return self.db.execute(
            """SELECT DISTINCT j.* FROM jobs j LEFT JOIN aliases a ON a.job_id=j.id
               WHERE j.url=? OR a.alias IN (?,?)
               ORDER BY (j.eligible_at IS NOT NULL) DESC, (j.delivery='sent') DESC,
               (j.delivery='uncertain') DESC, (j.delivery='pending') DESC, j.id""",
            (job.url, *keys),
        ).fetchall()

    def record(self, job: JobPosting, decision: Decision, now: datetime, *, keep_approved: bool = False) -> int:
        stamp = now.astimezone(timezone.utc).isoformat()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            matches = self.lookup(job)
            if not matches:
                row = self.db.execute(
                    "INSERT INTO jobs(url,posting,decision,collected_at) VALUES(?,?,?,?)",
                    (job.url, job.model_dump_json(), decision.model_dump_json(), stamp),
                )
                job_id = row.lastrowid
                delivery = "filtered"
                was_approved = False
            else:
                keep = matches[0]
                job_id, delivery = keep["id"], keep["delivery"]
                was_approved = keep["eligible_at"] is not None
                # Uma URL antes anônima pode ganhar empresa e unir dois registros sem duplicar links.
                for duplicate in matches[1:]:
                    self.db.execute("UPDATE OR IGNORE aliases SET job_id=? WHERE job_id=?", (job_id, duplicate["id"]))
                    self.db.execute("DELETE FROM aliases WHERE job_id=?", (duplicate["id"],))
                    if keep["status"] == "nova" and duplicate["status"] != "nova":
                        self.db.execute("UPDATE jobs SET status=? WHERE id=?", (duplicate["status"], job_id))
                    if not was_approved and duplicate["eligible_at"] is not None:
                        self.db.execute(
                            "UPDATE jobs SET eligible_at=?, delivery=? WHERE id=?",
                            (duplicate["eligible_at"], duplicate["delivery"], job_id),
                        )
                        was_approved = True
                        delivery = duplicate["delivery"]
                    self.db.execute("DELETE FROM jobs WHERE id=?", (duplicate["id"],))
                if not keep["url"]:
                    self.db.execute("UPDATE OR IGNORE jobs SET url=? WHERE id=?", (job.url, job_id))
            self.db.executemany(
                "INSERT OR IGNORE INTO aliases VALUES(?,?,?)",
                [(key, job_id, job.source) for key in ("url:" + job.url, "id:" + job.id_hash)],
            )
            if was_approved:
                # Vaga já aprovada e salva nunca é apagada do histórico em buscas futuras.
                if decision.eligible:
                    approved_state = delivery if delivery in ("sent", "uncertain") else "pending"
                    self.db.execute(
                        "UPDATE jobs SET posting=?, decision=?, delivery=? WHERE id=?",
                        (job.model_dump_json(), decision.model_dump_json(), approved_state, job_id),
                    )
            elif delivery == "filtered" or (not keep_approved and decision.eligible):
                approved_state = delivery if delivery in ("sent", "uncertain") else "pending"
                self.db.execute(
                    """UPDATE jobs SET posting=?, decision=?, delivery=?,
                       eligible_at=CASE WHEN ? THEN COALESCE(eligible_at, ?) ELSE NULL END WHERE id=?""",
                    (
                        job.model_dump_json(),
                        decision.model_dump_json(),
                        approved_state if decision.eligible else "filtered",
                        decision.eligible,
                        stamp,
                        job_id,
                    ),
                )
        return job_id

    def approved(self):
        return self.db.execute("SELECT * FROM jobs WHERE eligible_at IS NOT NULL ORDER BY eligible_at DESC, id DESC")

    def finish_hunt(self, hours: int, listings: int, sources_ok: int, sources_total: int, errors: int):
        with self.db:
            self.db.execute("INSERT INTO hunts(finished,hours,listings,sources_ok,sources_total,errors) VALUES(?,?,?,?,?,?)",
                            (datetime.now(timezone.utc).isoformat(), hours, listings, sources_ok, sources_total, errors))

    def last_hunt(self):
        return self.db.execute("SELECT * FROM hunts ORDER BY id DESC LIMIT 1").fetchone()

    def checkpoint(self, source: str) -> datetime | None:
        row = self.db.execute("SELECT completed_at FROM checkpoints WHERE source=?", (source,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def complete(self, source: str, now: datetime):
        with self.db:
            self.db.execute("""INSERT INTO checkpoints VALUES(?,?) ON CONFLICT(source)
                               DO UPDATE SET completed_at=max(completed_at,excluded.completed_at)""",
                            (source, now.astimezone(timezone.utc).isoformat()))

    def seen_urls(self, source: str) -> set[str]:
        return {r[0][4:] for r in self.db.execute(
            "SELECT alias FROM aliases WHERE source=? AND alias LIKE 'url:%'", (source,))}

    def pending(self, cutoff: datetime):
        return self.db.execute("""SELECT * FROM jobs WHERE delivery='pending' AND eligible_at>=?
                                  AND status IN ('nova','salva') ORDER BY eligible_at,id""",
                               (cutoff.astimezone(timezone.utc).isoformat(),)).fetchall()

    def claim(self, job_id: int) -> bool:
        # Se o processo cair após a requisição, a próxima rodada evita um segundo envio.
        with self.db:
            return self.db.execute("""UPDATE jobs SET delivery='uncertain' WHERE id=?
                                      AND delivery='pending' AND status IN ('nova','salva')""", (job_id,)).rowcount == 1

    def delivered(self, job_id: int):
        with self.db:
            self.db.execute("UPDATE jobs SET delivery='sent', notified_at=? WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(), job_id))

    def retry(self, job_id: int) -> bool:
        with self.db:
            return self.db.execute("UPDATE jobs SET delivery='pending' WHERE id=? AND delivery='uncertain'",
                                   (job_id,)).rowcount == 1

    def set_status(self, job_id: int, status: Status) -> bool:
        with self.db:
            return self.db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id)).rowcount == 1

    def latest(self):
        return self.db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
