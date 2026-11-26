import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from bs4 import BeautifulSoup

from src.collectors.base import Context
from src.collectors.careers import Amazon, Ashby, Greenhouse, Himalayas, ProgramaThor, WeWorkRemotely
from src.collectors.community import Communities
from src.collectors.hackernews import HackerNews
from src.collectors.public_page import public_page
from src.collectors.web import WebSearch, web_posting
from src.filters.rules import evaluate, evaluate_fit


def collect(cls, source, settings, now, handler, monkeypatch):
    async def instant(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", instant)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ctx = Context(settings, now, 36, None, set(), client)
            collector = cls(source, ctx)
            return [j async for j in collector.collect()], collector
    return asyncio.run(run())


def test_ashby_uses_explicit_mode_and_public_postings(settings, now, monkeypatch):
    settings.ashby_boards = {"startup": "Startup"}
    base = dict(title="Junior Software Engineer", jobUrl="https://jobs.ashbyhq.com/startup/1",
                publishedAt=now.isoformat(), isRemote=True, location="Brazil", workplaceType="Hybrid")
    rows = [base, {**base, "jobUrl": base["jobUrl"]+"x", "isListed": False},
            {**base, "jobUrl": base["jobUrl"]+"r", "workplaceType": "Remote", "descriptionHtml": "<p>Python</p>"}]
    jobs, board = collect(Ashby, "ashby", settings, now, lambda _: httpx.Response(200, json={"jobs": rows}), monkeypatch)
    assert board.successes == 1 and len(jobs) == 2
    assert not evaluate_fit(jobs[0], settings).eligible and evaluate_fit(jobs[1], settings).eligible


def test_greenhouse_reads_details_and_never_uses_edit_date_as_publication(settings, now, monkeypatch):
    settings.greenhouse_boards = {"empresa": "Empresa"}
    row = {"id": 1, "title": "Junior Developer", "absolute_url": "https://empresa.test/job/1",
           "updated_at": now.isoformat(), "location": {"name": "Remote - Brazil"}}

    def respond(request):
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [row, {**row, "id": 2}]})
        info = {"content": "&lt;p&gt;Application: &lt;a href='https://forms.gle/Teste'&gt;apply&lt;/a&gt;&lt;/p&gt;"}
        if request.url.path.endswith("/1"):
            info["first_published"] = (now - timedelta(days=40)).isoformat()
        return httpx.Response(200, json=info)
    jobs, _ = collect(Greenhouse, "greenhouse", settings, now, respond, monkeypatch)
    assert len(jobs) == 2 and all("https://forms.gle/Teste" in j.description for j in jobs)
    assert all(not evaluate(j, settings, now-timedelta(hours=36), now).eligible for j in jobs)


def test_amazon_and_himalayas_use_source_dates_and_country(settings, now, monkeypatch):
    def amazon(request):
        assert request.url.params["normalized_country_code[]"] == "BRA"
        return httpx.Response(200, json={"jobs": [{"title": "Software Development Engineer I",
                "location": "Virtual, BRA", "posted_date": "November 25, 2026", "updated_time": "2099-01-01",
                "job_path": "/en/jobs/42/engineer", "basic_qualifications": "Python"}]})
    jobs, _ = collect(Amazon, "amazon", settings, now, amazon, monkeypatch)
    assert jobs[0].is_remote and jobs[0].published_at.date() == now.date() and jobs[0].precision == "day"

    def himalayas(request):
        assert request.url.params["country"] == "BR"
        row = {"title": "Developer", "applicationLink": "https://himalayas.app/companies/example/jobs/42",
               "pubDate": int(now.timestamp()), "expiryDate": int(now.timestamp()) + 80000, "seniority": ["Entry-level"]}
        return httpx.Response(200, json={"jobs": [row, {**row, "expiryDate": 1, "applicationLink": row["applicationLink"]+"x"}]})
    jobs, _ = collect(Himalayas, "himalayas", settings, now, himalayas, monkeypatch)
    assert jobs and all(j.published_at is None and j.updated_at == now and evaluate_fit(j, settings).eligible for j in jobs)


def test_discovery_reads_original_and_can_fall_back_to_rss(settings, now, monkeypatch):
    from src.collectors import web
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings.web_queries = ["formulário"]
    visited = []

    def index(request):
        if "yahoo" in request.url.host or "duckduckgo" in request.url.host:
            return httpx.Response(202, html="<form id='challenge'></form>")
        return httpx.Response(200, text="""<rss><channel>
        <item><link>https://empresa.test/vaga</link><title>Dev Jr remoto</title></item>
        <item><link>https://empresa.test/hibrida</link><title>Dev Jr 100% remoto!</title></item>
        <item><link>https://empresa.test/vaga?utm_source=A</link><title>Dev Jr</title></item>
        </channel></rss>""")

    async def detail(_, url):
        visited.append(url)
        row = {"@type": "JobPosting", "title": "Dev Jr", "hiringOrganization": {"name": "Pequena"},
               "description": "Trabalho híbrido." if url.endswith("hibrida") else "100% remoto. Inscreva-se: https://forms.gle/abc",
               "applicantLocationRequirements": {"@type": "Country", "name": "Brazil"},
               "jobLocationType": "TELECOMMUTE", "datePosted": now.isoformat()}
        return url, BeautifulSoup('<script type="application/ld+json">' + json.dumps({"@graph": [row]}) + '</script>', "html.parser")
    monkeypatch.setattr(web, "public_page", detail)
    jobs, board = collect(WebSearch, "web", settings, now, index, monkeypatch)
    assert board.errors == 1 and board.successes == 1 and len(visited) == len(jobs) == 2
    assert jobs[0].form_url and jobs[0].company == "Pequena" and evaluate_fit(jobs[0], settings).eligible
    assert not evaluate_fit(jobs[1], settings).eligible


def test_direct_form_requires_open_recruitment_and_modality_in_header(settings, now):
    ctx = Context(settings, now, 36, None, set(), None)
    url = "https://docs.google.com/forms/d/e/abc/viewform"

    def page(title, intro, body):
        return BeautifulSoup(f'<meta property="og:title" content="{title}"><div class="cBGGJ">{intro}</div>'
                             f'<form action="/forms/d/e/abc/formResponse">{body}</form>', "html.parser")
    assert not web_posting(url, page("Pesquisa com Dev Jr", "Trabalho remoto", "Currículo"), ctx)
    assert not web_posting(url, page("Vaga Dev Jr", "100% remoto", "Não está mais aceitando respostas"), ctx)
    unclear = web_posting(url, page("Candidatura Dev Jr", "Python", "Modalidade desejada: 100% remoto?"), ctx)
    assert unclear and not evaluate_fit(unclear, settings).eligible
    good = web_posting(url, page("Vaga Dev Jr", "Trabalho 100% remoto. Python.", "Nome"), ctx)
    assert good.form_status == "open" and good.precision == "first_seen" and good.published_at is None
    assert evaluate_fit(good, settings).eligible


def test_community_keeps_form_with_its_own_post_and_searches_older_announcements(settings, now, monkeypatch):
    from src.core.forms import application_form
    settings.community_channels = ["StartupBR"]
    seen = []

    def respond(request):
        term = request.url.params.get("q", "")
        seen.append(term)
        if "before" in request.url.params:
            return httpx.Response(200, html="<html></html>")
        return httpx.Response(200, html=f'''<div class="tgme_widget_message" data-post="StartupBR/42">
        <time datetime="{(now-timedelta(days=5)).isoformat()}"></time>
        <div class="tgme_widget_message_text">#junior<br>#remoto<br>Startup | Developer Jr<br>Inscreva-se:
        <a href="https://forms.gle/Startup42">Formulário</a></div></div>''')
    jobs, _ = collect(Communities, "comunidades", settings, now, respond, monkeypatch)
    assert {"", "forms.gle", "docs.google.com/forms"} <= set(seen)
    assert jobs and all(application_form(j.description) == "https://forms.gle/Startup42" for j in jobs)
    assert all(j.url == "https://t.me/StartupBR/42" and evaluate_fit(j, settings).eligible for j in jobs)


def test_hackernews_reads_employer_comments_only(settings, now, monkeypatch):
    def respond(request):
        if "author_" in request.url.params["tags"]:
            return httpx.Response(200, json={"hits": [{"title": "Ask HN: Who is hiring? (November 2026)", "objectID": "123"}]})
        row = {"parent_id": 123, "objectID": "234", "created_at": now.isoformat(),
               "comment_text": "Startup | Junior Developer | REMOTE (LATAM)<p>Apply: https://forms.gle/ABC</p>"}
        return httpx.Response(200, json={"hits": [row, {**row, "parent_id": 234}]})
    jobs, _ = collect(HackerNews, "hackernews", settings, now, respond, monkeypatch)
    assert jobs and all(j.title == "Junior Developer" and j.company == "Startup" and evaluate_fit(j, settings).eligible for j in jobs)


def test_wwr_extracts_employer_form_and_region(settings, now, monkeypatch):
    from src.core.forms import application_form
    feed = """<rss><channel><item><title>Co: Junior Developer</title><region>Anywhere in the World</region>
              <description><![CDATA[<p>How to apply: <a href="https://forms.gle/Co123">our form</a></p>]]></description>
              <pubDate>Tue, 24 Nov 2026 20:00:00 GMT</pubDate><link>https://weworkremotely.com/remote-jobs/co-junior</link>
              </item></channel></rss>"""
    jobs, _ = collect(WeWorkRemotely, "weworkremotely", settings, now, lambda _: httpx.Response(200, text=feed), monkeypatch)
    assert jobs[0].company == "Co" and evaluate_fit(jobs[0], settings).eligible
    assert application_form(jobs[0].description) == "https://forms.gle/Co123"


def test_external_page_revalidates_redirect_before_next_get(monkeypatch):
    from src.collectors import public_page as reader
    hosts = []

    async def resolve(host):
        hosts.append(host)
        if host == "internal.test":
            raise ValueError("Destino não público")
        return "93.184.216.34"

    def respond(request):
        assert request.headers["Host"] == "corp.test" and request.extensions["sni_hostname"] == "corp.test"
        return httpx.Response(302, headers={"Location": "https://internal.test/path"})
    monkeypatch.setattr(reader, "public_address", resolve)

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(ValueError):
                await public_page(client, "https://corp.test/jobs/1")
    asyncio.run(check())
    assert hosts == ["corp.test", "internal.test"]


def test_yahoo_search_unwraps_ru_skips_noise_and_reads_generic_forms(settings, now, monkeypatch):
    from src.collectors import web
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings.web_queries = ["desenvolvedor"]
    settings.web_pages_per_query = 1
    visited = []
    long_desc = (
        "Estamos ampliando nossa equipe técnica com foco em APIs e serviços web escaláveis para novos produtos. "
        "Buscamos pessoa desenvolvedora backend júnior para atuação 100% remota com Python e SQL."
    )
    assert len(long_desc) > 180

    def index(request):
        assert "yahoo" in request.url.host
        return httpx.Response(200, html="""
        <div class="algo"><a href="https://r.search.yahoo.com/RU=https%3a%2f%2fdocs.google.com%2fforms%2f%3fhl%3dpt-br/RK=2">Google Forms Login</a></div>
        <div class="algo"><a href="https://r.search.yahoo.com/RU=https%3a%2f%2fpt.wikipedia.org%2fwiki%2fSoftware/RK=2">Desenvolvedor</a></div>
        <div class="algo"><a href="https://r.search.yahoo.com/RU=https%3a%2f%2fdocs.google.com%2fforms%2fd%2fe%2fOpenJob123%2fviewform/RK=2">Formulário de Candidatura</a></div>
        """)

    async def detail(_, url):
        visited.append(url)
        html = (
            f'<meta property="og:title" content="Formulário de Candidatura">'
            f'<meta property="og:description" content="{long_desc}">'
            f'<form action="/forms/d/e/OpenJob123/formResponse">Currículo</form>'
        )
        return url, BeautifulSoup(html, "html.parser")

    monkeypatch.setattr(web, "public_page", detail)
    jobs, board = collect(WebSearch, "web", settings, now, index, monkeypatch)
    assert board.errors == 0 and board.successes == 1
    assert visited == ["https://docs.google.com/forms/d/e/OpenJob123/viewform"]
    assert len(jobs) == 1 and evaluate_fit(jobs[0], settings).eligible
    ctx = Context(settings, now, 36, None, set(), None)
    assert not web_posting(
        "https://docs.google.com/forms/d/e/Noise123/viewform",
        BeautifulSoup('<meta property="og:title" content="Mentoria Individual Dev"><form action="/forms/d/e/Noise123/formResponse">Currículo</form>', "html.parser"),
        ctx,
    )


def test_community_parses_structured_cards_and_inline_hashtags(settings, now, monkeypatch):
    settings.community_channels = ["vagasbackend"]

    def respond(request):
        if "before" in request.url.params or request.url.params.get("q"):
            return httpx.Response(200, html="<html></html>")
        return httpx.Response(200, html=f'''<div class="tgme_widget_message" data-post="vagasbackend/99">
        <time datetime="{now.isoformat()}"></time>
        <div class="tgme_widget_message_text">
        🚨 Nova vaga encontrada 🚨<br>
        📜 Título: Desenvolvedor #Python Júnior<br>
        🏢 Empresa: Empresa Teste<br>
        🌍 Local: Brasil<br>
        👨🏽‍💻 Atuação: 100% remoto<br>
        Candidatura: <a href="https://forms.gle/Tg99">Formulário</a>
        </div></div>''')

    jobs, _ = collect(Communities, "comunidades", settings, now, respond, monkeypatch)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Desenvolvedor Python Júnior"
    assert job.company == "Empresa Teste" and job.location == "Brasil"
    assert job.form_url == "https://forms.gle/Tg99"
    assert evaluate_fit(job, settings).eligible


def test_programathor_reads_remote_jsonld_job_posting(settings, now, monkeypatch):
    def respond(request):
        if "/jobs-city/remoto" in request.url.path:
            return httpx.Response(200, html='<a href="/jobs/33925-desenvolvedor-a-junior">Desenvolvedor Júnior</a>')
        payload = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Desenvolvedor(a) Júnior",
            "description": "<p>Python e SQL. Inscreva-se: https://forms.gle/PThor1</p>",
            "datePosted": now.date().isoformat(),
            "validThrough": (now + timedelta(days=10)).isoformat(),
            "hiringOrganization": {"@type": "Organization", "name": "ThorCorp"},
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": [{"@type": "Country", "name": "Brazil"}],
        }
        return httpx.Response(200, html=f'<script type="application/ld+json">{json.dumps(payload)}</script>')

    jobs, board = collect(ProgramaThor, "programathor", settings, now, respond, monkeypatch)
    assert board.successes == 1 and board.errors == 0 and len(jobs) == 1
    assert jobs[0].company == "ThorCorp" and jobs[0].form_url == "https://forms.gle/PThor1"
    assert jobs[0].is_remote and evaluate_fit(jobs[0], settings).eligible
