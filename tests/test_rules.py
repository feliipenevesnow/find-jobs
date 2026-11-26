from datetime import timedelta

import pytest

from src.core.models import canonical_url
from src.filters.rules import evaluate, requires_experience


def test_normalize_url_preserves_identity():
    assert canonical_url("https://BR.indeed.com/viewjob?utm_medium=x&jk=12&refId=A#detalhes") == "https://br.indeed.com/viewjob?jk=12"
    assert canonical_url("https://acme.gupy.io/job/eyJqb2JJZCI6MTIzfQ==?jobBoardSource=gupy_portal") == "https://acme.gupy.io/jobs/123"
    with pytest.raises(ValueError):
        canonical_url("javascript:alert(1)")


@pytest.mark.parametrize(("text","blocked"), [
    ("Mínimo de 3 anos de experiência com Python", True), ("4+ years of experience", True),
    ("Experiência mínima de 5 anos", True), ("3+ anos", True), ("Três anos de atuação com APIs", True),
    ("Somos uma empresa com 12 anos de mercado", False), ("Experiência de 1 ano", False),
    ("Experiência de 3 anos é um diferencial", False), ("De 1 a 3 anos de experiência", False),
    ("Experiência de 1 a 3 anos", False), ("4 anos de desenvolvimento de sistemas", True),
    ("Diferenciais:\n4 anos de experiência em Python.", False),
    ("Diferenciais:\nPython.\nRequisitos:\n4 anos de experiência profissional.", True),
])
def test_required_years(text, blocked):
    assert requires_experience(text) is blocked


@pytest.mark.parametrize(("title","extra","accepted"), [
    ("DESENVOLVEDOR MOBILE JÚNIOR - Capão Raso", {}, True),
    ("Full Stack Jr.", {}, True), ("Trainee em Programação", {}, True),
    ("Estágio em Desenvolvimento de Embalagens", {}, False),
    ("Estágio Programação de TV", {}, False),
    ("Estágio - Atuação na Gerência de Desenvolvimento de Negócios", {}, False),
    ("Software Engineer II, Backend - Emerging Products", {"level":"Entry level"}, False),
    ("Desenvolvedor Pleno / Júnior", {}, False), ("Desenvolvedor PL/SQL Jr", {}, True),
    ("Junior Software Engineer", {"source":"linkedin","location":"Denver, CO, USA"}, False),
    ("Junior Software Engineer", {"source":"linkedin","location":"Exterior","is_remote":True}, True),
    ("Desenvolvedor Jr", {"description":"Experiência de 5 anos com Java"}, False),
])
def test_profile_examples(job, settings, now, title, extra, accepted):
    item = job.model_copy(update={"title":title, **extra})
    assert evaluate(item, settings, now-timedelta(hours=2), now).eligible is accepted


def test_discovery_requires_recent_inventory(job, settings, now):
    item = job.model_copy(update={"precision":"discovery","published_at":None})
    cutoff = now-timedelta(hours=2)
    assert not evaluate(item, settings, cutoff, now).eligible
    assert not evaluate(item, settings, cutoff, now, now-timedelta(days=1)).eligible
    assert evaluate(item, settings, cutoff, now, now-timedelta(minutes=40)).eligible


def test_day_precision_and_city(job, settings, now):
    item = job.model_copy(update={"precision":"day","published_at":now.replace(hour=3)})
    assert not evaluate(item, settings, now-timedelta(hours=2), now).eligible
    settings.cities = ["São Paulo"]
    assert not evaluate(job.model_copy(update={"is_remote":False}), settings, now-timedelta(days=1), now).eligible
    assert evaluate(job.model_copy(update={"is_remote":True}), settings, now-timedelta(days=1), now).eligible
    settings.remote = False
    assert not evaluate(job, settings, now-timedelta(days=1), now).eligible
    assert evaluate(job.model_copy(update={"is_remote":False, "location":"São Paulo"}), settings, now-timedelta(days=1), now).eligible
