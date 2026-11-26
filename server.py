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

from src.core.config import MAX_SEARCH_HOURS, Settings
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
FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    b'<rect width="64" height="64" rx="14" fill="#175841"/>'
    b'<path d="M24 20v-3a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v3h8a4 4 0 0 1 4 4v22a4 4 0 0 1-4 4H16a4 4 0 0 1-4-4V24a4 4 0 0 1 4-4h8zm4-2v2h8v-2h-8zm-12 9v7h32v-7H16zm0 11v8h32v-8H36v2a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2H16z" fill="#f6f7f0"/>'
    b'</svg>'
)
SERVER_STATE = {
    "started_at": None,
    "last_attempt_ts": 0.0,
    "last_error": None,
    "current_reason": None,
}

LIVE_UI_SNIPPET = """
<style>
  .live-controls, .hunt-form { display: inline-flex; align-items: end; gap: .6rem; flex-wrap: wrap; }
  .hunt-form select, .hunt-form input, .hunt-btn { min-height: 2.4rem; }
  .hunt-form input { max-width: 6rem; }
  .hunt-message { color: var(--muted); font-size: .85rem; }
  .hunt-btn {
    appearance: none;
    border: 1px solid var(--pine);
    background: var(--pine);
    color: #fff;
    border-radius: .5rem;
    padding: .42rem .85rem;
    font: inherit;
    font-size: .86rem;
    font-weight: 650;
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

    const form = document.createElement('form');
    form.className = 'hunt-form';
    form.innerHTML =
      '<label>Período da busca<select name="period">' +
      '<option value="">Padrão do perfil (__DEFAULT_HOURS__ h)</option>' +
      '<option value="24">Últimas 24 horas</option><option value="48">Últimas 48 horas</option>' +
      '<option value="72">Últimos 3 dias</option><option value="168">Últimos 7 dias</option>' +
      '<option value="336">Últimos 14 dias</option><option value="720">Últimos 30 dias</option>' +
      '<option value="custom">Personalizado</option></select></label>' +
      '<label hidden>Horas<input name="hours" type="number" min="1" max="__MAX_HOURS__" step="1" required disabled></label>' +
      '<button type="submit" class="hunt-btn" id="trigger-hunt-btn">↻ Buscar vagas agora</button>';
    wrap.appendChild(form);
    const message = document.createElement('span');
    message.setAttribute('role', 'status');
    message.className = 'hunt-message';
    wrap.appendChild(message);
    const btn = form.querySelector('button');
    const period = form.elements.period;
    const hours = form.elements.hours;
    period.addEventListener('change', () => {
      hours.disabled = period.value !== 'custom';
      hours.parentElement.hidden = hours.disabled;
      if (!hours.disabled) hours.focus();
    });

    let polling = false;
    function startPolling() {
      if (polling) return;
      polling = true;
      btn.disabled = true;
      period.disabled = true;
      hours.disabled = true;
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

    form.addEventListener('submit', async event => {
      event.preventDefault();
      const value = period.value === 'custom' ? hours.valueAsNumber : Number(period.value);
      message.textContent = '';
      btn.disabled = true;
      btn.textContent = 'Iniciando busca...';
      try {
        const res = await fetch('/api/hunt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(period.value ? { hours: value } : {})
        });
        if (res.ok) {
          startPolling();
        } else if (res.status === 409) {
          message.textContent = 'Já há uma busca em andamento. Aguarde para iniciar outra.';
          startPolling();
        } else {
          message.textContent = (await res.json()).error;
        }
      } catch (_) {
        message.textContent = 'Falha ao iniciar a busca. Verifique a conexão com o servidor.';
      } finally {
        if (!polling) {
          btn.disabled = false;
          btn.textContent = '↻ Buscar vagas agora';
        }
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
  const formEl = document.querySelector('#form-filter');
  if (formEl) formEl.addEventListener('change', applyExtraFilters);
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


def ensure_panel_exists(settings: Settings, config_path: Path | None = None) -> Path:
    panel_path = settings.database.parent / "painel.html"
    template = ROOT / "src" / "panel.html"
    inputs = [template, template.with_suffix(".py"), settings.resume]
    if config_path:
        inputs.append(config_path)
    if not panel_path.is_file() or any(p.is_file() and p.stat().st_mtime > panel_path.stat().st_mtime for p in inputs):
        repo = Repository.for_settings(settings)
        try:
            write_panel(repo, settings.database.parent, settings)
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
    return _run_locked_hunt(config_path, hours_override, alarm_slot, skip_ai, extend_forms=True)


def start_manual_hunt(config_path: Path, hours: int | None) -> bool:
    if not HUNT_LOCK.acquire(blocking=False):
        return False
    try:
        threading.Thread(target=_run_locked_hunt, args=(config_path, hours, None, False),
                         kwargs={"extend_forms": hours is None}, daemon=True).start()
    except RuntimeError:
        HUNT_LOCK.release()
        raise
    return True


def _run_locked_hunt(config_path: Path, hours_override: int | None, alarm_slot: str | None,
                     skip_ai: bool, *, extend_forms: bool) -> bool:
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
        hours = hours_override or settings.hours

        with closing(sqlite3.connect(gate, timeout=1800)) as lock:
            lock.execute("BEGIN EXCLUSIVE")
            repo = Repository.for_settings(settings)
            if alarm_slot:
                cp = repo.checkpoint(alarm_slot)
                if cp and cp.astimezone(BR).date() == datetime.now(BR).date():
                    write_panel(repo, settings.database.parent, settings)
                    return True
            log.info("Iniciando coleta de vagas (motivo=%s, janela=%dh)...", SERVER_STATE["current_reason"], hours)
            code = asyncio.run(hunt(settings, repo, hours, False, skip_ai, console, extend_forms=extend_forms))
            if code == 0 and alarm_slot:
                repo.complete(alarm_slot, datetime.now(timezone.utc))
            write_panel(repo, settings.database.parent, settings)
        if code != 0:
            SERVER_STATE["last_error"] = "Nenhuma fonte respondeu à busca."
        return code == 0
    except Exception as exc:
        SERVER_STATE["last_error"] = str(exc)
        log.exception("Erro durante a coleta em segundo plano: %s", exc)
        return False
    finally:
        if repo is not None:
            repo.db.close()
        SERVER_STATE["current_reason"] = None
        HUNT_LOCK.release()


def read_jobs_map_and_stats(settings: Settings) -> tuple[dict, dict]:
    now_br = datetime.now(BR)
    repo = Repository.for_settings(settings)
    try:
        jobs_map = {}
        approved_count = 0
        for row in repo.visible_approved(settings.remote):
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


def render_live_panel(config_path: Path, auto_hunt: bool = True) -> bytes:
    settings = Settings.read(config_path.resolve())
    panel_path = ensure_panel_exists(settings, config_path)
    html = panel_path.read_text(encoding="utf-8")
    jobs_map, stats = read_jobs_map_and_stats(settings)
    alarm_info = {"next_label": stats["next_alarm"]} if auto_hunt else None
    injected = (
        LIVE_UI_SNIPPET
        .replace("__JOBS_MAP_JSON__", json.dumps(jobs_map, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__INITIAL_HUNTING__", "true" if HUNT_LOCK.locked() else "false")
        .replace("__DEFAULT_HOURS__", str(settings.hours))
        .replace("__MAX_HOURS__", str(MAX_SEARCH_HOURS))
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
    repo = Repository.for_settings(settings)
    try:
        cp_morning = repo.checkpoint("alarme_manha")
        cp_evening = repo.checkpoint("alarme_noite")
        last = repo.last_hunt()
        has_approved = next(repo.visible_approved(settings.remote), None) is not None
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


def make_handler(config_path: Path, auto_hunt: bool = True):
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

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or "0")
            if not 0 <= length <= 4096:
                raise ValueError("Tamanho de requisição inválido")
            data = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(data, dict):
                raise ValueError("Informe um objeto JSON")
            return data

        def do_HEAD(self) -> None:
            self._handle_get(head_only=True)

        def do_GET(self) -> None:
            self._handle_get(head_only=False)

        def _handle_get(self, head_only: bool) -> None:
            parsed = urlparse(self.path)
            route = parsed.path

            if route in ("/favicon.ico", "/favicon.svg"):
                self._send_bytes(HTTPStatus.OK, FAVICON_SVG, "image/svg+xml", head_only=head_only)
                return

            if route == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "hunting": HUNT_LOCK.locked()}, head_only=head_only)
                return

            if route == "/api/status":
                settings = Settings.read(config_path.resolve())
                _, stats = read_jobs_map_and_stats(settings)
                if not auto_hunt:
                    stats["next_alarm"] = None
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "hunting": HUNT_LOCK.locked(),
                        "started_at": SERVER_STATE["started_at"],
                        "last_error": SERVER_STATE["last_error"],
                        "current_reason": SERVER_STATE["current_reason"],
                        **stats,
                    },
                    head_only=head_only,
                )
                return

            if route in ("/", "/index.html", "/painel.html"):
                body = render_live_panel(config_path, auto_hunt)
                self._send_bytes(HTTPStatus.OK, body, "text/html; charset=utf-8", head_only=head_only)
                return

            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.end_headers()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path

            if route == "/api/hunt":
                try:
                    payload = self._read_json()
                    hours = payload.get("hours")
                    if payload and (payload.keys() != {"hours"} or type(hours) is not int
                                    or not 1 <= hours <= MAX_SEARCH_HOURS):
                        raise ValueError("Período inválido")
                except ValueError:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Informe de 1 a {MAX_SEARCH_HOURS} horas inteiras."})
                    return
                if not start_manual_hunt(config_path, hours):
                    self._send_json(HTTPStatus.CONFLICT, {"ok": False, "hunting": True, "error": "Já há uma busca em andamento."})
                    return
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    {"ok": True, "hunting": True, "message": "Coleta iniciada em segundo plano."},
                )
                return

            if route == "/api/status":
                try:
                    payload = self._read_json()
                    job_id = int(payload.get("id"))
                    status = str(payload.get("status", "")).strip()
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Payload invalido."})
                    return

                if status not in STATUSES:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Status invalido."})
                    return

                settings = Settings.read(config_path.resolve())
                repo = Repository.for_settings(settings)
                try:
                    updated = repo.set_status(job_id, status)
                    if updated:
                        write_panel(repo, settings.database.parent, settings)
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

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.config, auto_hunt=not args.no_auto_hunt))
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
