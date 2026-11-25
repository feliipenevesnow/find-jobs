import json
import sqlite3
import threading
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest
import yaml

import server
from src.core.models import Decision
from src.panel import write_panel
from src.storage.repository import Repository


def test_http_server_serves_live_panel_and_updates_status(tmp_path, job, settings):
    db_path = tmp_path / "data" / "vagas.sqlite3"
    resume_path = tmp_path / "curriculo.local.md"
    resume_path.write_text("Python, FastAPI e SQL.", encoding="utf-8")

    cfg_data = settings.model_dump(mode="json")
    cfg_data["database"] = "data/vagas.sqlite3"
    cfg_data["resume"] = "curriculo.local.md"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_data, allow_unicode=True), encoding="utf-8")

    repo = Repository(db_path)
    with closing(repo.db):
        job_id = repo.record(job, Decision(eligible=True, reason="ok", score=9.0), datetime.now(timezone.utc))
        repo.finish_hunt(36, 5, 1, 1, 0)
        write_panel(repo, db_path.parent)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cfg_path))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] is True

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert job.title in html
            assert "Buscar vagas agora" in html
            assert "Alarmes diários ativos" in html

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/status",
            data=json.dumps({"id": job_id, "status": "candidatado"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["ok"] is True

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
            st = json.loads(resp.read().decode("utf-8"))
            assert st["approved_count"] == 1
            assert "next_alarm" in st

        check_repo = Repository(db_path)
        with closing(check_repo.db):
            row = next(iter(check_repo.approved()))
            assert row["status"] == "candidatado"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_unique_url_constraint_and_saved_job_never_overwritten(tmp_path, job, now):
    db_path = tmp_path / "vagas.sqlite3"
    repo = Repository(db_path)
    with closing(repo.db):
        first_id = repo.record(job, Decision(eligible=True, reason="ok", score=8.5), now)
        repo.set_status(first_id, "salva")

        # Segunda coleta trazendo o mesmo link (mesmo com tracking extra) e decisão inelegível não pode apagar nem duplicar
        same_link_with_utm = job.model_copy(update={"url": job.url + "?utm_campaign=teste"})
        second_id = repo.record(same_link_with_utm, Decision(eligible=False, reason="Nota abaixo do mínimo", score=2.0), now)
        assert second_id == first_id

        approved_rows = list(repo.approved())
        assert len(approved_rows) == 1
        assert approved_rows[0]["status"] == "salva"
        assert approved_rows[0]["url"] == job.url

        # Garante índice UNIQUE real na coluna url no SQLite
        with pytest.raises(sqlite3.IntegrityError):
            repo.db.execute(
                "INSERT INTO jobs(url, posting, decision, collected_at) VALUES(?,?,?,?)",
                (job.url, job.model_dump_json(), "{}", now.isoformat()),
            )


def test_morning_and_evening_alarm_windows():
    morning = datetime(2026, 11, 25, 8, 5, tzinfo=server.BR)
    evening = datetime(2026, 11, 25, 18, 10, tzinfo=server.BR)

    # Manhã às 08:05 cobre desde 00:00 do dia anterior (32h+ -> ~35h)
    m_hours = server.compute_morning_hours(morning)
    assert 33 <= m_hours <= 36

    # Fim do dia às 18:10 (quando manhã já rodou) cobre o dia atual (18h+ -> 20h)
    e_hours_after_morning = server.compute_evening_hours(evening, morning_done_today=True)
    assert 19 <= e_hours_after_morning <= 22

    # Fim do dia às 18:10 (se manhã NÃO rodou) expande para cobrir o dia anterior também (42h+)
    e_hours_catchup = server.compute_evening_hours(evening, morning_done_today=False)
    assert e_hours_catchup >= 43
