import argparse
import asyncio
import json
import logging
import math
import os
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, time as dt_time, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.console import Console

from src.core.config import Settings
from src.core.hunt import hunt
from src.core.models import JobPosting
from src.panel import write_panel
from src.storage.repository import Repository

ROOT = Path(__file__).resolve().parent
BR = ZoneInfo("America/Sao_Paulo")
MORNING_ALARM_HOUR = 8   # 08:00 -> busca vagas do dia anterior até a manhã atual
EVENING_ALARM_HOUR = 18  # 18:00 -> busca vagas publicadas ao longo do dia atual
STATUSES = ("nova", "salva", "candidatado", "entrevista", "descartada")
log = logging.getLogger("findjobs.server")

HUNT_LOCK = threading.Lock()
SERVER_STATE = {
    "hunting": False,
    "started_at": None,
    "last_attempt_ts": 0.0,
    "last_error": None,
    "current_reason": None,
}

LIVE_UI_SNIPPET = """
<style>
  .live-controls { display: inline-flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
  .hunt-btn {
    appearance: none;
    border: 1px solid var(--pine);
    background: var(--pine);
    color: #fff;
    border-radius: .5rem;
    padding: .42rem .85rem;
    font: 650 .84rem/1.2 inherit;
    cursor: pointer;
    transition: opacity .15s ease;
  }
  .hunt-btn:hover:not(:disabled) { opacity: .9; }
  .hunt-btn:disabled { opacity: .65; cursor: wait; }
  .alarm-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: .5rem 1rem;
    background: #eef4ef;
    border: 1px solid var(--line);
    border-radius: .65rem;
    padding: .7rem 1rem;
    margin: -1rem 0 1.4rem;
    font-size: .86rem;
    color: var(--ink);
  }
  .alarm-banner strong { color: var(--pine); }
  .status-select {
    min-height: 2.05rem;
    padding: .22rem .55rem;
    font-size: .82rem;
    border-radius: .45rem;
    border: 1px solid #aabaae;
    background: #f8faf7;
    color: var(--ink);
  }
  .actions-right { display: inline-flex; align-items: center; gap: .65rem; flex-wrap: wrap; margin-left: auto; }
</style>
<script>
(function() {
  const JOBS_MAP = __JOBS_MAP_JSON__;
  const INITIAL_HUNTING = __INITIAL_HUNTING__;
  const ALARM_INFO = __ALARM_INFO_JSON__;
  const LABELS = {
    nova: 'Nova',
    salva: 'Salva',
    candidatado: 'Candidatado',
    entrevista: 'Entrevista',
    descartada: 'Descartada'
  };

  const summaryEl = document.querySelector('.summary');
  if (summaryEl && ALARM_INFO) {
    const banner = document.createElement('div');
    banner.className = 'alarm-banner';
    banner.innerHTML =
      '<span><strong>Alarmes diários ativos:</strong> 08:00 (vagas do dia anterior) e 18:00 (vagas do dia atual) · ' +
      'Próxima busca automática: <strong>' + ALARM_INFO.next_label + '</strong></span>' +
      '<span>Histórico salvo no SQLite (link único sem duplicatas)</span>';
    summaryEl.insertAdjacentElement('afterend', banner);
  }

  const tools = document.querySelector('.tools');
  let statusFilter = null;
  let minScoreFilter = null;
  if (tools) {
    const stLabel = document.createElement('label');
    stLabel.textContent = 'Candidatura';
    statusFilter = document.createElement('select');
    statusFilter.id = 'status-filter';
    for (const [val, txt] of [
      ['ativas', 'Ativas (ocultar descartadas)'],
      ['todas', 'Todas'],
      ['nova', 'Somente novas'],
      ['salva', 'Salvas'],
      ['candidatado', 'Candidatado'],
      ['entrevista', 'Entrevista'],
      ['descartada', 'Descartadas']
    ]) {
      const o = document.createElement('option');
      o.value = val;
      o.textContent = txt;
      statusFilter.appendChild(o);
    }
    stLabel.appendChild(statusFilter);
    tools.appendChild(stLabel);

    const scLabel = document.createElement('label');
    scLabel.textContent = 'Nota mínima';
    minScoreFilter = document.createElement('select');
    minScoreFilter.id = 'min-score-filter';
    for (const [val, txt] of [
      ['0', 'Todas as aprovadas'],
      ['5', 'Nota ≥ 5,0'],
      ['6.5', 'Nota ≥ 6,5'],
      ['7.5', 'Nota ≥ 7,5']
    ]) {
      const o = document.createElement('option');
      o.value = val;
      o.textContent = txt;
      minScoreFilter.appendChild(o);
    }
    scLabel.appendChild(minScoreFilter);
    tools.appendChild(scLabel);
  }

  const bar = document.querySelector('.bar');
  if (bar) {
    const stamp = bar.querySelector('.stamp');
    const wrap = document.createElement('div');
    wrap.className = 'live-controls';
    if (stamp) {
      bar.replaceChild(wrap, stamp);
      wrap.appendChild(stamp);
    } else {
      bar.appendChild(wrap);
    }

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'hunt-btn';
    btn.id = 'trigger-hunt-btn';
    btn.textContent = INITIAL_HUNTING ? 'Buscando nos portais...' : '↻ Buscar vagas agora';
    btn.disabled = INITIAL_HUNTING;
    wrap.appendChild(btn);

    let polling = false;
    function startPolling() {
      if (polling) return;
      polling = true;
      btn.disabled = true;
      btn.textContent = 'Buscando nos portais...';
      const timer = setInterval(async () => {
        try {
          const res = await fetch('/api/status', { cache: 'no-store' });
          if (!res.ok) return;
          const data = await res.json();
          if (!data.hunting) {
            clearInterval(timer);
            window.location.reload();
          }
        } catch (_) {}
      }, 3000);
    }

    if (INITIAL_HUNTING) startPolling();

    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Iniciando busca...';
      try {
        const res = await fetch('/api/hunt', { method: 'POST' });
        if (res.ok) {
          startPolling();
        } else {
          btn.disabled = false;
          btn.textContent = '↻ Buscar vagas agora';
        }
      } catch (_) {
        btn.disabled = false;
        btn.textContent = '↻ Buscar vagas agora';
      }
    });
  }

  function applyExtraFilters() {
    if (typeof update === 'function') update();
    const stVal = statusFilter ? statusFilter.value : 'todas';
    const minSc = minScoreFilter ? Number(minScoreFilter.value) : 0;
    let visible = 0;
    const allCards = document.querySelectorAll('#grid .job');
    for (const card of allCards) {
      if (card.hidden) continue;
      const st = card.dataset.status || 'nova';
      const sc = Number(card.dataset.score || '0');
      const statusOk =
        stVal === 'todas' ? true :
        stVal === 'ativas' ? st !== 'descartada' :
        st === stVal;
      if (!statusOk || sc < minSc) {
        card.hidden = true;
      } else {
        visible++;
      }
    }
    const countEl = document.querySelector('#count');
    if (countEl) {
      countEl.textContent = (visible === 1 ? '1 oportunidade exibida' : visible + ' oportunidades exibidas') +
        ' (' + allCards.length + ' salvas no banco)';
    }
    const emptyEl = document.querySelector('#empty');
    if (emptyEl) {
      emptyEl.hidden = visible > 0;
      if (allCards.length && !visible) {
        emptyEl.textContent = 'Nenhuma vaga corresponde aos filtros selecionados acima.';
      }
    }
  }

  for (const card of document.querySelectorAll('.job')) {
    const openLink = card.querySelector('a.open');
    const actions = card.querySelector('.actions');
    if (!openLink || !actions) continue;
    const info = JOBS_MAP[openLink.getAttribute('href')];
    if (!info) continue;
    card.dataset.status = info.status;

    const rightBox = document.createElement('div');
    rightBox.className = 'actions-right';

    const select = document.createElement('select');
    select.className = 'status-select';
    select.setAttribute('aria-label', 'Status da candidatura');
    for (const [val, label] of Object.entries(LABELS)) {
      const opt = document.createElement('option');
      opt.value = val;
      opt.textContent = label;
      if (val === info.status) opt.selected = true;
      select.appendChild(opt);
    }

    select.addEventListener('change', async () => {
      const nextStatus = select.value;
      select.disabled = true;
      try {
        const res = await fetch('/api/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: info.id, status: nextStatus })
        });
        if (res.ok) {
          info.status = nextStatus;
          card.dataset.status = nextStatus;
          const meta = card.querySelector('.meta');
          if (meta) {
            const parts = meta.textContent.split(' · ');
            const base = parts.slice(0, 2).join(' · ');
            meta.textContent = nextStatus === 'nova' ? base : (base + ' · ' + LABELS[nextStatus]);
          }
          applyExtraFilters();
        }
      } catch (_) {
      } finally {
        select.disabled = false;
      }
    });

    actions.replaceChild(rightBox, openLink);
    rightBox.appendChild(select);
    rightBox.appendChild(openLink);
  }

  if (statusFilter) statusFilter.addEventListener('change', applyExtraFilters);
  if (minScoreFilter) minScoreFilter.addEventListener('change', applyExtraFilters);
  const searchEl = document.querySelector('#search');
  const periodEl = document.querySelector('#period');
  const sortEl = document.querySelector('#sort');
  if (searchEl) searchEl.addEventListener('input', applyExtraFilters);
  if (periodEl) periodEl.addEventListener('change', applyExtraFilters);
  if (sortEl) sortEl.addEventListener('change', applyExtraFilters);
  applyExtraFilters();
})();
</script>
"""


def compute_morning_hours(now_br: datetime) -> int:
    """No começo do dia, busca desde 00:00 do dia anterior até agora."""
    yesterday_midnight = datetime.combine(now_br.date() - timedelta(days=1), dt_time(0, 0), tzinfo=BR)
    hours = math.ceil((now_br - yesterday_midnight).total_seconds() / 3600) + 2
    return max(26, min(72, hours))


def compute_evening_hours(now_br: datetime, morning_done_today: bool) -> int:
    """No final do dia, busca todo o dia atual (00:00 de hoje até agora). Se a manhã não rodou, cobre ontem também."""
    if not morning_done_today:
        return compute_morning_hours(now_br)
    today_midnight = datetime.combine(now_br.date(), dt_time(0, 0), tzinfo=BR)
    hours = math.ceil((now_br - today_midnight).total_seconds() / 3600) + 1
    return max(12, min(30, hours))


def next_alarm_label(now_br: datetime) -> str:
    if now_br.hour < MORNING_ALARM_HOUR:
        return f"hoje às {MORNING_ALARM_HOUR:02d}:00 (vagas de ontem)"
    if now_br.hour < EVENING_ALARM_HOUR:
        return f"hoje às {EVENING_ALARM_HOUR:02d}:00 (vagas de hoje)"
    return f"amanhã às {MORNING_ALARM_HOUR:02d}:00 (vagas do dia anterior)"


def ensure_panel_exists(settings: Settings) -> Path:
    panel_path = settings.database.parent / "painel.html"
    if not panel_path.is_file():
        repo = Repository(settings.database)
        try:
            write_panel(repo, settings.database.parent)
        finally:
            repo.db.close()
    return panel_path


def run_hunt_sync(
    config_path: Path,
    *,
    hours_override: int | None = None,
    alarm_slot: str | None = None,
    skip_ai: bool = False,
) -> bool:
    if not HUNT_LOCK.acquire(blocking=False):
        return False

    SERVER_STATE["hunting"] = True
    SERVER_STATE["started_at"] = datetime.now(BR).isoformat()
    SERVER_STATE["last_attempt_ts"] = time.time()
    SERVER_STATE["last_error"] = None
    SERVER_STATE["current_reason"] = alarm_slot or "manual"

    repo = None
    try:
        load_dotenv(ROOT / ".env", override=True)
        settings = Settings.read(config_path.resolve())
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        gate = settings.database.with_name(settings.database.name + ".lock")
        console = Console(highlight=False)
        hours = hours_override or compute_morning_hours(datetime.now(BR))

        with closing(sqlite3.connect(gate, timeout=1800)) as lock:
            lock.execute("BEGIN EXCLUSIVE")
            repo = Repository(settings.database)
            log.info("Iniciando coleta de vagas (motivo=%s, janela=%dh)...", SERVER_STATE["current_reason"], hours)
            code = asyncio.run(hunt(settings, repo, hours, False, skip_ai, console))
            if code == 0 and alarm_slot:
                repo.complete(alarm_slot, datetime.now(timezone.utc))
            write_panel(repo, settings.database.parent)
        return True
    except Exception as exc:
        SERVER_STATE["last_error"] = str(exc)
        log.exception("Erro durante a coleta em segundo plano: %s", exc)
        return False
    finally:
        if repo is not None:
            repo.db.close()
        SERVER_STATE["hunting"] = False
        SERVER_STATE["current_reason"] = None
        HUNT_LOCK.release()


def read_jobs_map_and_stats(settings: Settings) -> tuple[dict, dict]:
    now_br = datetime.now(BR)
    repo = Repository(settings.database)
    try:
        jobs_map = {}
        approved_count = 0
        for row in repo.approved():
            approved_count += 1
            posting = JobPosting.model_validate_json(row["posting"])
            jobs_map[posting.url] = {
                "id": row["id"],
                "status": row["status"],
            }
        last = repo.last_hunt()
        cp_morning = repo.checkpoint("alarme_manha")
        cp_evening = repo.checkpoint("alarme_noite")
        stats = {
            "approved_count": approved_count,
            "last_finished": last["finished"] if last else None,
            "sources_ok": last["sources_ok"] if last else 0,
            "sources_total": last["sources_total"] if last else 0,
            "morning_done_today": bool(cp_morning and cp_morning.astimezone(BR).date() == now_br.date()),
            "evening_done_today": bool(cp_evening and cp_evening.astimezone(BR).date() == now_br.date()),
            "next_alarm": next_alarm_label(now_br),
        }
        return jobs_map, stats
    finally:
        repo.db.close()


def render_live_panel(config_path: Path) -> bytes:
    settings = Settings.read(config_path.resolve())
    panel_path = ensure_panel_exists(settings)
    html = panel_path.read_text(encoding="utf-8")
    jobs_map, stats = read_jobs_map_and_stats(settings)
    alarm_info = {"next_label": stats["next_alarm"]}
    injected = (
        LIVE_UI_SNIPPET
        .replace("__JOBS_MAP_JSON__", json.dumps(jobs_map, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__INITIAL_HUNTING__", "true" if SERVER_STATE["hunting"] else "false")
        .replace("__ALARM_INFO_JSON__", json.dumps(alarm_info, ensure_ascii=False).replace("</", "<\\/"))
    )
    if "</body>" in html:
        html = html.replace("</body>", injected + "\n</body>", 1)
    else:
        html += injected
    return html.encode("utf-8")


def check_and_fire_alarms(config_path: Path) -> None:
    now_br = datetime.now(BR)
    settings = Settings.read(config_path.resolve())
    repo = Repository(settings.database)
    try:
        cp_morning = repo.checkpoint("alarme_manha")
        cp_evening = repo.checkpoint("alarme_noite")
        last = repo.last_hunt()
        has_approved = repo.approved().fetchone() is not None
    finally:
        repo.db.close()

    morning_done = bool(cp_morning and cp_morning.astimezone(BR).date() == now_br.date())
    evening_done = bool(cp_evening and cp_evening.astimezone(BR).date() == now_br.date())

    # Evita loop de retentativa imediata em caso de queda de internet (aguarda 20 min)
    if SERVER_STATE["last_error"] and (time.time() - SERVER_STATE["last_attempt_ts"] < 1200):
        return

    # 1) Primeira execução ou banco ainda sem oportunidades salvas hoje
    if last is None or (not morning_done and MORNING_ALARM_HOUR <= now_br.hour < EVENING_ALARM_HOUR):
        hours = compute_morning_hours(now_br)
        slot = "alarme_manha" if now_br.hour >= MORNING_ALARM_HOUR else None
        run_hunt_sync(config_path, hours_override=hours, alarm_slot=slot)
        return

    # 2) Alarme do fim do dia (a partir das 18:00): busca as vagas do dia atual
    if now_br.hour >= EVENING_ALARM_HOUR and not evening_done:
        hours = compute_evening_hours(now_br, morning_done)
        run_hunt_sync(config_path, hours_override=hours, alarm_slot="alarme_noite")
        return

    # 3) Se ligou antes das 08:00 e ainda não tem histórico salvo, garante uma coleta inicial
    if not has_approved and not morning_done and (time.time() - SERVER_STATE["last_attempt_ts"] >= 1800):
        run_hunt_sync(config_path, hours_override=compute_morning_hours(now_br), alarm_slot=None)


def background_scheduler(config_path: Path) -> None:
    time.sleep(2.5)
    while True:
        try:
            check_and_fire_alarms(config_path)
        except Exception as exc:
            log.warning("Aviso no verificador de alarmes: %s", exc)
        time.sleep(60)


def make_handler(config_path: Path):
    class FindJobsHandler(BaseHTTPRequestHandler):
        def _send_bytes(self, status: int, body: bytes, content_type: str, head_only: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _send_json(self, status: int, payload: dict, head_only: bool = False) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status, raw, "application/json; charset=utf-8", head_only=head_only)

        def do_HEAD(self) -> None:
            self._handle_get(head_only=True)

        def do_GET(self) -> None:
            self._handle_get(head_only=False)

        def _handle_get(self, head_only: bool) -> None:
            parsed = urlparse(self.path)
            route = parsed.path

            if route == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return

            if route == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "hunting": SERVER_STATE["hunting"]}, head_only=head_only)
                return

            if route == "/api/status":
                settings = Settings.read(config_path.resolve())
                _, stats = read_jobs_map_and_stats(settings)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "hunting": SERVER_STATE["hunting"],
                        "started_at": SERVER_STATE["started_at"],
                        "last_error": SERVER_STATE["last_error"],
                        "current_reason": SERVER_STATE["current_reason"],
                        **stats,
                    },
                    head_only=head_only,
                )
                return

            if route in ("/", "/index.html", "/painel.html"):
                body = render_live_panel(config_path)
                self._send_bytes(HTTPStatus.OK, body, "text/html; charset=utf-8", head_only=head_only)
                return

            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.end_headers()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path

            if route == "/api/hunt":
                if not SERVER_STATE["hunting"]:
                    now_br = datetime.now(BR)
                    threading.Thread(
                        target=run_hunt_sync,
                        args=(config_path,),
                        kwargs={"hours_override": compute_morning_hours(now_br), "alarm_slot": None},
                        daemon=True,
                    ).start()
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    {"ok": True, "hunting": True, "message": "Coleta iniciada em segundo plano."},
                )
                return

            if route == "/api/status":
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
                    job_id = int(payload.get("id"))
                    status = str(payload.get("status", "")).strip()
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Payload invalido."})
                    return

                if status not in STATUSES:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Status invalido."})
                    return

                settings = Settings.read(config_path.resolve())
                repo = Repository(settings.database)
                try:
                    updated = repo.set_status(job_id, status)
                    if updated:
                        write_panel(repo, settings.database.parent)
                finally:
                    repo.db.close()

                self._send_json(
                    HTTPStatus.OK if updated else HTTPStatus.NOT_FOUND,
                    {"ok": updated},
                )
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Rota nao encontrada."})

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

    return FindJobsHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Servidor web do painel Find Jobs.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml", help="Arquivo YAML de configuracao")
    parser.add_argument("--host", default="0.0.0.0", help="Host de escuta HTTP")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8090")), help="Porta HTTP")
    parser.add_argument("--no-auto-hunt", action="store_true", help="Desativar alarmes automaticos em segundo plano")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    load_dotenv(ROOT / ".env", override=False)

    settings = Settings.read(args.config.resolve())
    ensure_panel_exists(settings)

    if not args.no_auto_hunt:
        threading.Thread(target=background_scheduler, args=(args.config,), daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.config))
    log.info("Servidor Find Jobs ativo em http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
