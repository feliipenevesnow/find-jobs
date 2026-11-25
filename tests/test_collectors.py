import asyncio
from datetime import timedelta

import httpx
import pytest

from src.collectors.base import Context
from src.collectors.gupy import Gupy
from src.collectors.github import Github
from src.collectors.jobspy import JobSpy
from src.collectors.monitor_parsers import _texto_para_data
from src.collectors.portals import HtmlBoard, Nube


async def instant(_):
    pass


def collect(cls, name, settings, now, handler, *, last=None, known=None):
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = cls(name,Context(settings,now,2,last,known or set(),client))
            return [j async for j in source.collect()],source
    return asyncio.run(exercise())


def test_gupy_detail_and_publication(settings, now, monkeypatch):
    monkeypatch.setattr(asyncio,"sleep",instant)
    settings.search_terms=["desenvolvedor"]
    def handler(request):
        if request.url.host == "portal.gupy.io":
            assert request.url.path.endswith("/api/job-search/jobs")
            return httpx.Response(200,json={"data":[{"name":"Desenvolvedor Jr","careerPageName":"Exemplo",
                "publishedDate":now.isoformat(),"jobUrl":"https://exemplo.gupy.io/jobs/23","workplaceType":"remote"}]})
        return httpx.Response(200,text='<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"job":{"description":"<p>Python</p>","prerequisites":"SQL"}}}}</script>')
    jobs,source=collect(Gupy,"gupy",settings,now,handler)
    assert len(jobs)==1 and jobs[0].is_remote and jobs[0].published_at==now
    assert "Python" in jobs[0].description and "SQL" in jobs[0].description and source.errors==0


def test_github_isolates_failing_repo_and_skips_pr(settings, now, monkeypatch):
    monkeypatch.setattr(asyncio,"sleep",instant)
    settings.github_repos=["broken/repo","good/repo"]
    def handler(request):
        if "broken" in request.url.path:
            return httpx.Response(403)
        row={"title":"[Remoto] Dev Jr na Empresa","html_url":"https://github.com/good/repo/issues/7",
             "created_at":now.isoformat(),"body":"Python","labels":[{"name":"Júnior"}]}
        return httpx.Response(200,json=[{**row,"pull_request":{}},row,{**row,"created_at":(now-timedelta(days=5)).isoformat()}])
    jobs,source=collect(Github,"github",settings,now,handler)
    assert source.errors==source.successes==1
    assert len(jobs)==1 and jobs[0].company=="Empresa" and jobs[0].is_remote


@pytest.mark.parametrize("name,html",[
    ("infojobs", '<script type="application/ld+json">{"@type":"JobPosting","title":"Dev Jr","url":"/vaga-de-dev__77.aspx","datePosted":"2026-11-25","description":"Python"}</script>'),
    ("vagascom", '<li><a href="/vagas/v77/dev" title="Dev Jr">Dev Jr</a><span>Hoje</span></li>')])
def test_html_boards(settings, now, monkeypatch, name, html):
    monkeypatch.setattr(asyncio,"sleep",instant)
    settings.search_terms=["dev"]
    jobs,source=collect(HtmlBoard,name,settings,now,lambda _:httpx.Response(200,text=html))
    assert source.successes==1 and jobs[0].title=="Dev Jr" and jobs[0].url.startswith("https://")


def test_nube_baseline_and_next_inventory(settings, now, monkeypatch):
    monkeypatch.setattr(asyncio,"sleep",instant)
    url="https://www.nube.com.br/detalhes-vaga/421/vaga-de-estagio-em-desenvolvimento-de-software-sao-paulo-sp-bolsa-de-1500-mais-beneficios"
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith(".xml"):
            return httpx.Response(200,text=f"<urlset><url><loc>{url}</loc></url></urlset>")
        return httpx.Response(200,json={"param":{"requisito":{"linhas":{"atividades":{"mostrar":1,"texto":"Python"}}}}})
    initial,_=collect(Nube,"nube",settings,now,handler)
    assert len(initial)==1 and initial[0].precision=="discovery" and len(calls)==1
    repeated,_=collect(Nube,"nube",settings,now,handler,last=now,known={url})
    assert not repeated and len(calls)==2
    new,_=collect(Nube,"nube",settings,now,handler,last=now)
    assert len(new)==1 and "Python" in new[0].description and len(calls)==4


def test_relative_hour_keeps_precision():
    from datetime import datetime, timezone
    age = datetime.now(timezone.utc) - _texto_para_data("Há 4 horas")
    assert abs(age.total_seconds() - 14400) < 2


def test_jobspy_native_window_and_clean_url(settings, now, monkeypatch):
    import json
    settings.search_terms = ["dev"]
    async def launch(*args, **kwargs):
        assert args[-4:] == ("indeed", "dev", "2", str(settings.results_per_search))
        class Result:
            returncode = 0
            async def communicate(self):
                rows = [{"title":"Dev Jr","company":"A","location":"Curitiba, Brazil",
                         "job_url":"https://br.indeed.com/viewjob?jk=123&utm_source=teste",
                         "date_posted":"2026-11-25","description":"SQL","min_amount":5000,"currency":"BRL"}]
                return json.dumps(rows).encode(), b""
        return Result()
    monkeypatch.setattr(asyncio,"create_subprocess_exec",launch)
    monkeypatch.setattr(asyncio,"sleep",instant)
    jobs,source = collect(JobSpy,"indeed",settings,now,lambda _:httpx.Response(500))
    assert source.successes == 1 and jobs[0].precision == "native" and "5000" in jobs[0].salary
    assert jobs[0].url == "https://br.indeed.com/viewjob?jk=123"
