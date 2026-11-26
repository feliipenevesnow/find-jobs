import re
from datetime import datetime, timedelta

from src.core.config import Settings
from src.core.forms import google_form_url
from src.core.models import Decision, Evidence, JobDetails, JobPosting, normalized, plain
from src.filters.profile import extra_credit, has_term, relevant_evidence
from src.filters.workplace import available_from_brazil, workplace


def contains_any(text: str, terms: list[str]) -> bool:
    return any(has_term(text, term) for term in terms)


YEARS = r"(?:[3-9]|\d{2,}|tres|quatro|cinco|seis|sete|oito|nove|dez)"
UNIT = r"\s*\+?\s*(?:anos?|years?)\b"
EXPERIENCE = re.compile(
    rf"\b{YEARS}{UNIT}\s+(?:ou mais\s+)?(?:de\s+)?(?:experiencia|atuacao|vivencia|pratica|exp\b)"
    rf"|\b{YEARS}{UNIT}(?:\s+of)?\s+(?:professional\s+|relevant\s+|work\s+)?experience"
    rf"|\b(?:experiencia|experience|vivencia|atuacao|exigimos|exigencia|minimo|pelo menos|at least)"
    rf"[^.;!?\n]{{0,45}}?(?<!\d){YEARS}{UNIT}"
    rf"|\b{YEARS}\s*\+\s*(?:anos|years)\b"
    rf"|\b{YEARS}{UNIT}\s+(?:com|em|usando|with|using)\s+\w+"
    rf"|\b{YEARS}{UNIT}\s+de\s+(?:desenvolvimento|programacao|trabalho)\b"
)
OPTIONAL = re.compile(r"\b(desejavel|diferencial|opcional|preferred|nice.to.have|nao exigimos|nao (?:e )?(?:necessari[oa]|obrigatori[oa]))\b")

DISCOVERY_WINDOW = "Inventário inicial ou intervalo de descoberta fora da janela"
OLD_DAY = "O dia informado pelo portal é anterior à janela"
OUTSIDE_WINDOW = "Publicação fora da janela ou sem data verificável"
TIME_FILTERS = {DISCOVERY_WINDOW, OLD_DAY, OUTSIDE_WINDOW}
ENTRY_DESCRIPTION = re.compile(
    r"\b(?:sem experiencia (?:previa|profissional)|nao (?:e necessario ter|exigimos) experiencia|"
    r"primeir[oa] (?:emprego|oportunidade profissional)|aceitamos (?:iniciantes|recem.formados)|"
    r"no (?:prior|professional|previous) experience (?:is )?(?:required|necessary)|"
    r"recent graduates (?:are )?(?:welcome|encouraged)|entry.level (?:role|position)|"
    r"(?:vaga|nivel|senioridade|posicao)\s*[:=-]?\s*(?:junior|jr|trainee))\b"
)
LEVEL_FIELD = re.compile(r"(?:^|[\n.;|])\s*(?:nivel(?: da vaga| de experiencia)?|senioridade(?: da vaga)?|seniority(?: level)?)"
                        r"\s*[:=–-]\s*([^\n.;|!?]{1,60})", re.M)


def location_rejection(job: JobDetails, settings: Settings) -> str | None:
    mode, _ = workplace(job)
    if settings.remote:
        if mode == "unknown":
            return "Trabalho remoto não confirmado no anúncio"
        if mode != "remote":
            return "A vaga exige ou prevê trabalho presencial"
        if not available_from_brazil(job):
            return "Vaga remota com restrição geográfica fora do Brasil"
    elif mode == "remote":
        return "Modalidade remota desativada"
    else:
        city_match = any(has_term(normalized(job.location), normalized(c)) for c in settings.cities)
        if settings.cities and not city_match:
            return "Localização fora do perfil"
        if job.source in ("linkedin", "indeed") and not city_match and not contains_any(job.location, ["Brasil", "Brazil"]):
            return "Localização presencial no Brasil não confirmada"
    return None


def application_rejection(job: JobPosting) -> str | None:
    direct_boards = {"gupy", "amazon", "greenhouse", "ashby", "linkedin", "indeed", "vagascom", "infojobs"}
    if job.source not in direct_boards or google_form_url(job.url):
        if job.form_status == "closed":
            return "O formulário encerrou as inscrições"
        if job.form_status == "unrelated":
            return "O formulário não é uma candidatura para esta vaga"
    if job.precision == "first_seen" and job.form_status != "open":
        return "Formulário sem confirmação de inscrições abertas"
    return None


def requires_experience(description: str) -> bool:
    optional_section = False
    for part in re.split(r"[.!?;\n•]+", plain(description)):
        section = part.strip(" #*-:\t")
        if re.match(r"^(diferenciais|desejaveis|nice.to.have|preferred qualifications|bonus points)\b", section):
            optional_section = True
        if re.match(r"^(requisitos|obrigatorios|minimum qualifications|basic qualifications|required|requirements)\b", section):
            optional_section = False
        if optional_section and not re.search(r"\b(?:obrigatori[oa]|mandatory|required)\b", part):
            continue
        for match in EXPERIENCE.finditer(part):
            # Limites superiores de faixas (1 a 3 anos) não são o mínimo exigido.
            prefix = part[max(0, match.start()-25):match.start()]
            if re.search(r"(?:\b[012]\s*(?:a|ate|to|-)|\bate)\s*$", prefix) or re.search(
                rf"\b[012]\s*(?:a|ate|to|-)\s*{YEARS}{UNIT}", match.group()
            ):
                continue
            if not OPTIONAL.search(part):
                return True
    return False


def publication_rejection(job: JobPosting, cutoff: datetime, now: datetime, previous_nube: datetime | None,
                          first_seen: datetime | None, form_cutoff: datetime | None) -> str | None:
    if form_cutoff is not None and job.precision in ("timestamp", "day"):
        cutoff = form_cutoff
    if job.precision in ("first_seen", "updated"):
        when = first_seen if job.precision == "first_seen" else job.updated_at
        if not when or not cutoff <= when <= now:
            return OUTSIDE_WINDOW
    elif job.precision == "discovery":
        if previous_nube is None or previous_nube < cutoff:
            return DISCOVERY_WINDOW
    elif job.precision == "native":
        if job.published_at and job.published_at > now:
            return OUTSIDE_WINDOW
        if job.published_at and job.published_at + timedelta(days=1) <= cutoff:
            return OLD_DAY
    else:
        if not job.published_at or job.published_at < cutoff or job.published_at > now:
            return OUTSIDE_WINDOW
    return None


def evaluate(job: JobPosting, settings: Settings, cutoff: datetime, now: datetime,
             previous_nube: datetime | None = None, records: list[Evidence] | None = None,
             *, first_seen: datetime | None = None, form_cutoff: datetime | None = None) -> Decision:
    if not job.form_url or job.form_status != "open":
        form_cutoff = None
    elif form_cutoff is None:
        form_cutoff = min(cutoff, now - timedelta(days=settings.forms_lookback_days))
    if reason := publication_rejection(job, cutoff, now, previous_nube, first_seen, form_cutoff):
        return Decision(eligible=False, reason=reason)
    return evaluate_application(job, settings, records)


def evaluate_application(job: JobPosting, settings: Settings, records: list[Evidence] | None = None) -> Decision:
    if reason := application_rejection(job):
        return Decision(eligible=False, reason=reason)
    fit = evaluate_fit(job, settings, records)
    # Uma falha de leitura não desfaz uma incompatibilidade já confirmada no mesmo formulário.
    if fit.eligible and job.form_status == "listed" and job.form_details:
        last_verified = evaluate_fit(job.model_copy(update={"form_status": "open"}), settings, records)
        if not last_verified.eligible:
            return last_verified
    if fit.eligible and fit.score < settings.min_score:
        return fit.model_copy(update={"eligible": False, "reason": "Nota abaixo do mínimo"})
    return fit


def evaluate_fit(job: JobDetails, settings: Settings, records: list[Evidence] | None = None) -> Decision:
    if isinstance(job, JobPosting):
        job = job.profile_details
    text = job.title + " " + job.description
    evidence = relevant_evidence(text, records or [])

    def reject(reason: str) -> Decision:
        return Decision(eligible=False, reason=reason, evidence=evidence)

    title = re.sub(r"\bpl/sql\b", "", plain(job.title))
    level = " ".join([title, job.level, *LEVEL_FIELD.findall(plain(job.description))])
    if contains_any(level, settings.blocked) or re.search(
        r"\b(?:engineer|developer|desenvolvedor[a]?|engenheir[oa])\s+(?:ii|iii|iv|[2-4])\b", title
    ):
        if contains_any(title, ["jr", "júnior"]) and any(
            plain(term) in {"pl", "pleno"} and has_term(title, term) for term in settings.blocked
        ):
            return reject("Título misto júnior/pleno. O perfil bloqueia pleno.")
        return reject("Senioridade acima do perfil")
    if not contains_any(level, settings.positive) and not ENTRY_DESCRIPTION.search(plain(job.description)):
        return reject("Nível de entrada não identificado")
    if not contains_any(job.title, settings.roles):
        return reject("Cargo fora do perfil")
    # “Desenvolvimento” também nomeia áreas como negócios, produto e embalagens.
    specific_roles = [term for term in settings.roles if normalized(term) != "desenvolvimento"]
    if not contains_any(title, specific_roles):
        software_context = ["software", "sistemas", "programação", "web", "mobile", "aplicativos"]
        if not contains_any(title, software_context) and not contains_any(job.description, settings.stack):
            return reject("Desenvolvimento sem evidência de software")
    if contains_any(title, settings.excluded_areas):
        return reject("Área de desenvolvimento fora de software")
    if requires_experience(job.description):
        return reject("Exige três ou mais anos de experiência")
    if reason := location_rejection(job, settings):
        return reject(reason)
    skills = [s for s in settings.stack if has_term(text, s)]
    score = min(10, 6.5 + 0.7 * len(skills) + extra_credit(evidence, skills))
    return Decision(eligible=True, reason="Aprovada pelas regras", score=round(score, 1), skills=skills, evidence=evidence)
