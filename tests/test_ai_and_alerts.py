import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from src.core.models import Decision
from src.filters.ai import assess, parse_analysis
from src.notifiers.telegram import message, send, TelegramRejected

VALID = dict(score=8.5, senioridade_real="junior", skills_match=["SQL"], skills_faltantes=[],
             red_flags=[], resumo_curto="Pede SQL. Aceita projetos pessoais.")


@pytest.mark.parametrize("updates", [{"score":11}, {"score":"8"}, {"score":-1}, {"senioridade_real":"staff"},
                                     {"extra":"x"}, {"resumo_curto":"Um. Dois. Três."}])
def test_strict_model(updates):
    with pytest.raises(ValidationError):
        parse_analysis(json.dumps({**VALID, **updates}))


@pytest.mark.parametrize("provider", ["gemini","openai","groq","ollama"])
def test_providers_and_fallback(monkeypatch, job, provider):
    monkeypatch.setenv("AI_MODEL","modelo-teste")
    monkeypatch.setenv(provider.upper()+"_API_KEY","chave-teste")
    result = Decision(eligible=True,reason="regras",score=7)
    async def exercise():
        def respond(request):
            data = json.loads(request.content)
            assert job.title in json.dumps(data, ensure_ascii=False)
            payload = json.dumps(VALID)
            body = {"candidates":[{"content":{"parts":[{"text":payload}]}}]} if provider == "gemini" else (
                {"message":{"content":payload}} if provider == "ollama" else {"choices":[{"message":{"content":payload}}]})
            return httpx.Response(200,json=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            scored = await assess(client,provider,"Portfólio em SQL",job,result)
            assert scored.analysis and scored.eligible and scored.score == 8.5
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(429))) as client:
            fallback = await assess(client,provider,"SQL",job,result)
            assert fallback.ai_unavailable and fallback.eligible and fallback.score == 7
    asyncio.run(exercise())


def test_alert_html_and_acknowledgement(job):
    job.title = "<Dev & Dados>"
    body = message(job,Decision(eligible=True,reason="regras",ai_unavailable=True),2)
    assert "&lt;Dev &amp; Dados&gt;" in body and "[Análise IA indisponível]" in body
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(429))) as client:
            with pytest.raises(TelegramRejected):
                await send(client,"token","chat",body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"ok":True,"result":{"message_id":1}}))) as client:
            await send(client,"token","chat",body)
    asyncio.run(exercise())
