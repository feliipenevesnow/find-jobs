from datetime import datetime, timezone
from html import escape
import os

import httpx

from src.core.models import Decision, JobPosting


def timing(job: JobPosting, now: datetime, hours: int) -> str:
    if job.precision == "discovery":
        return "Descoberta desde a última leitura do Nube"
    if job.precision == "native":
        return f"Na janela de {hours} h do portal"
    if job.precision == "day" and job.published_at:
        return "Publicada em " + job.published_at.strftime("%d/%m/%Y")
    if job.published_at:
        minutes = max(0, int((now-job.published_at).total_seconds()//60))
        return f"Publicada há {minutes} min" if minutes < 60 else f"Publicada há {minutes//60} h"
    return "Data de publicação indisponível"


def message(job: JobPosting, decision: Decision, hours: int) -> str:
    def clean(value: str, limit: int = 170) -> str:
        return escape(value[:limit])

    lines = [f"<b>{clean(job.title)}</b>", f"⭐ {decision.score:.1f}/10 · {clean(job.company or 'Empresa não informada')}",
             f"{'Remoto' if job.is_remote else clean(job.location or 'Local a confirmar')} · {clean(job.source)}",
             timing(job, datetime.now(timezone.utc), hours)]
    if decision.skills:
        lines.append("Em comum: " + clean(", ".join(decision.skills)))
    for evidence in decision.evidence[:3]:
        lines.append(f"{clean(evidence.kind)}: {clean(evidence.fact, 190)}")
    if decision.analysis:
        lines.append(clean(decision.analysis.resumo_curto, 350))
        if decision.analysis.red_flags:
            lines.append("Atenção: " + clean(", ".join(decision.analysis.red_flags)))
    if decision.ai_unavailable:
        lines.append("[Análise IA indisponível]")
    lines.append(f'<a href="{escape(job.url, quote=True)}">Abrir candidatura</a>')
    return "\n".join(lines)


class TelegramRejected(Exception):
    """A API rejeitou explicitamente o envio."""


async def setup_bot(lookup_chat: bool) -> str:
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or (not lookup_chat and not chat):
        raise ValueError("Preencha TELEGRAM_TOKEN e, para testar o envio, TELEGRAM_CHAT_ID no .env.")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if not lookup_chat:
                await send(client, token, chat, "Teste do caçador de vagas. Seu Telegram está conectado.")
                return "Mensagem de teste entregue ao Telegram."
            response = await client.get(f"https://api.telegram.org/bot{token}/getUpdates")
            response.raise_for_status()
            updates = response.json()["result"]
            ids = {str(u["message"]["chat"]["id"]) for u in updates if u.get("message", {}).get("chat")}
            if ids:
                return "CHAT_ID das conversas recentes: " + ", ".join(sorted(ids))
            return "Envie /start ao seu bot e execute --chat-id novamente."
    except (TelegramRejected, httpx.HTTPError, ValueError, KeyError, TypeError):
        raise ValueError("Falha ao consultar o Telegram. Confira a conexão, o token e a conversa com o bot.") from None


async def send(client: httpx.AsyncClient, token: str, chat: str, text: str):
    response = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                       "link_preview_options": {"is_disabled": True}})
    if 400 <= response.status_code < 500:
        raise TelegramRejected(f"Telegram recusou o envio (HTTP {response.status_code})")
    response.raise_for_status()
    result = response.json()
    if result.get("ok") is False:
        raise TelegramRejected("Telegram recusou a mensagem")
    if result.get("ok") is not True or not result.get("result", {}).get("message_id"):
        raise ValueError("Resposta do Telegram inconclusiva")
