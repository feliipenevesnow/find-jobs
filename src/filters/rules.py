import re
from datetime import datetime, timedelta

from src.core.config import Settings
from src.core.models import Decision, Evidence, JobDetails, JobPosting, normalized, plain
from src.filters.profile import extra_credit, has_term, relevant_evidence


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


def requires_experience(description: str) -> bool:
    for part in re.split(r"[.!?;\n•]+", plain(description)):
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


def evaluate(job: JobPosting, settings: Settings, cutoff: datetime, now: datetime,
             previous_nube: datetime | None = None, records: list[Evidence] | None = None) -> Decision:
    def reject(reason: str) -> Decision:
        return Decision(eligible=False, reason=reason)

    if job.precision == "discovery":
        if previous_nube is None or previous_nube < cutoff:
            return reject(DISCOVERY_WINDOW)
    elif job.precision == "native":
        if job.published_at and job.published_at + timedelta(days=1) <= cutoff:
            return reject(OLD_DAY)
    else:
        if not job.published_at or job.published_at < cutoff or job.published_at > now:
            return reject(OUTSIDE_WINDOW)
    return evaluate_fit(job, settings, records)


def evaluate_fit(job: JobDetails, settings: Settings, records: list[Evidence] | None = None) -> Decision:
    text = job.title + " " + job.description
    evidence = relevant_evidence(text, records or [])

    def reject(reason: str) -> Decision:
        return Decision(eligible=False, reason=reason, evidence=evidence)

    title = re.sub(r"\bpl/sql\b", "", plain(job.title))
    if contains_any(title + " " + job.level, settings.blocked) or re.search(
        r"\b(?:engineer|developer|desenvolvedor[a]?|engenheir[oa])\s+(?:ii|iii|iv|[2-4])\b", title
    ):
        if contains_any(title, ["jr", "júnior"]) and any(
            plain(term) in {"pl", "pleno"} and has_term(title, term) for term in settings.blocked
        ):
            return reject("Título misto júnior/pleno. O perfil bloqueia pleno.")
        return reject("Senioridade acima do perfil")
    if not contains_any(job.title + " " + job.level, settings.positive):
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
    if job.is_remote:
        if not settings.remote:
            return reject("Modalidade remota desativada")
    else:
        city_match = any(has_term(normalized(job.location), normalized(c)) for c in settings.cities)
        if settings.cities and not city_match:
            return reject("Localização fora do perfil")
        if job.source in ("linkedin", "indeed") and not city_match and not contains_any(job.location, ["Brasil", "Brazil"]):
            return reject("Localização presencial no Brasil não confirmada")
    skills = [s for s in settings.stack if has_term(text, s)]
    score = min(10, 6.5 + 0.7 * len(skills) + extra_credit(evidence, skills))
    return Decision(eligible=True, reason="Aprovada pelas regras", score=round(score, 1), skills=skills, evidence=evidence)
