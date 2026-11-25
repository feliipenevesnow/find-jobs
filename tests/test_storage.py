from contextlib import closing

from src.core.models import Decision
from src.storage.repository import Repository


def test_cross_source_delivery_and_application(tmp_path, job, now):
    path = tmp_path / "jobs.db"
    allowed = Decision(eligible=True, reason="perfil", score=8)
    repo = Repository(path)
    first = repo.record(job, allowed, now)
    second = job.model_copy(update={"source":"linkedin", "company":"ACAO digital", "title":"DESENVOLVEDOR MOBILE JUNIOR!",
                                     "url":"https://linkedin.com/jobs/view/11"})
    assert repo.record(second, allowed, now) == first
    assert len(repo.pending(now)) == 1
    assert repo.claim(first)
    assert not repo.claim(first)
    repo.delivered(first)
    repo.set_status(first,"entrevista")
    repo.db.close()
    repo = Repository(path)
    with closing(repo.db):
        renamed = second.model_copy(update={"title":"Novo título"})
        assert repo.record(renamed, allowed, now) == first
        row = repo.latest()[0]
        assert row["status"] == "entrevista" and row["notified_at"]
        assert not repo.pending(now)


def test_preview_preserves_database_and_checkpoints(tmp_path, job, now):
    path = tmp_path / "jobs.sqlite3"
    repo = Repository(path, preview=True)
    with closing(repo.db):
        repo.record(job, Decision(eligible=True,reason="ok"), now)
        repo.complete("nube",now)
    assert not path.exists()
    real = Repository(path)
    with closing(real.db):
        real.record(job, Decision(eligible=True,reason="ok"), now)
    preview = Repository(path, preview=True)
    with closing(preview.db):
        assert len(preview.pending(now)) == 1
        preview.claim(1)
        preview.complete("nube", now)
    real = Repository(path)
    with closing(real.db):
        assert len(real.pending(now)) == 1
        assert real.checkpoint("nube") is None


def test_unknown_companies_keep_distinct_jobs(tmp_path, job, now):
    repo = Repository(tmp_path/"test", preview=True)
    with closing(repo.db):
        for suffix in ("01","02"):
            repo.record(job.model_copy(update={"company":"","url":"https://example.com/"+suffix}),
                        Decision(eligible=True,reason="ok"),now)
        assert len(repo.pending(now)) == 2


def test_uncertain_requires_explicit_release(tmp_path, job, now):
    repo = Repository(tmp_path/"test")
    with closing(repo.db):
        key = repo.record(job,Decision(eligible=True,reason="ok"),now)
        assert repo.claim(key)
        repo.record(job,Decision(eligible=True,reason="ok"),now)
        assert not repo.pending(now)
        assert repo.retry(key) and len(repo.pending(now)) == 1
