import re

from src.core.models import JobDetails, Workplace, plain


REMOTE = r"(?:remot[oa]s?|remote|home[ -]?office|home[ -]based|teletrabalho|telecommut(?:e|ing)|work(?:ing)? from home)"
OFFICE = r"(?:presencia(?:l|is)|hibrid[oa]s?|hybrid|on[ -]?site|in[ -]person)"
TECHNICAL = re.compile(r"\b(?:hybrid (?:cloud|apps?|systems?|environments?)|(?:nuvem|aplicacao|arquitetura|ambiente)s? hibrid[oa]s?|remote (?:access|support|desktop|sensing|procedure)|(?:acesso|suporte|monitoramento|atendimento) remot[oa])\b")
NEGATION = re.compile(rf"\b(?:nao|sem|not|no|non)[ -]+(?:(?:e|sera|oferecemos|permite|aceita|ha|is|a|fully|totalmente|100\s*%|trabalho|work|modelo|opcao|possibilidade|de|em|trabalhamos)\s+){{0,5}}{REMOTE}\b"
                      rf"|\b{REMOTE}\s+(?:nao (?:disponivel|permitido)|is not (?:available|allowed))")
ONSITE = re.compile(
    rf"\b(?:modelo|modalidade|regime|formato|trabalho|trabalh[oa]r?|atuacao|vaga|posicao|position|workplace|role|work model)"
    rf"\s*(?:de trabalho|de atuacao)?\s*[:=-]?\s*(?:(?:e|is|a|em|no|modelo|formato|regime|100%|fully)\s+)*{OFFICE}\b"
    rf"|\b{OFFICE}\s+(?:work(?:ing)?|role|position|schedule|obrigatori[oa]|em\s+\w+)\b"
    r"|\b(?:na empresa e home office|retorno (?:ao escritorio|presencial)|comparecimento (?:presencial|obrigatorio))\b"
    rf"|\b(?:presenca|encontros?|reunioes|treinamento|onboarding|visitas?|trabalho)\s+{OFFICE}\s+(?:obrigatori\w*|seman\w*|mens\w*|trimestr\w*)"
    r"|\b(?:obrigatori\w*|mandatory|required|regular)\s+(?:\w+ ){0,2}(?:in[ -]person|on[ -]?site|office attendance|presencial)"
    r"|\b(?:must|required to|precisa|necessario|obrigatorio|disponibilidade para)\s+(?:\w+\s+){0,4}(?:escritorio|office|presencialmente|presencial)\b"
    r"|\b(?:\d+|um|dois|duas|tres|one|two|three|once|twice)\s*(?:x|vezes|dias?|days?)?\s*(?:(?:por|na|a|per|ao|no|each)\s+)?(?:semana|week|mes|month)\s+(?:(?:in|the|at|no|na|do|de)\s+)*(?:escritorio|empresa|office|presencia\w*)"
    r"|\b(?:ida|idas|presenca)\s+(?:ao|no)\s+escritorio\s+(?:obrigatoria|semanal|mensal)"
    r"|\b(?:com |exige |inclui )(?:idas?|visitas?|presenca|reunioes|encontros?)\s+(?:eventuais? |ocasionais? )?(?:ao escritorio|no escritorio|presenciais?)\b(?!\s+opciona)"
    r"|\b(?:onboarding|treinamento inicial|integracao inicial)\s+(?:e |sera )?presencial\b(?!\s+opcional)"
    rf"|\b{REMOTE}\s+(?:apenas |somente |only )?(?:\d+|um|dois|one|two)\s+(?:dias?|days?)\b"
    rf"|\b{REMOTE}\s+(?:parcial|ocasional|temporari[oa]|ate (?:a|o)|apenas (?:as|nas|aos)|during onboarding)\b"
    r"|\b(?:dias?|days?)\s+(?:(?:por semana|per week|a week)\s+)?(?:no escritorio|na empresa|presenciais|in (?:the )?office|onsite)\b"
    rf"|\b{OFFICE}\s*(?:[:,–-]\s*(?:\d|[a-z]+ dias?)|com (?:possibilidade de )?home office)"
    rf"|\b{REMOTE}\s*(?:[/|]|\bou\b|\bor\b)\s*{OFFICE}\b"
)
REMOTE_TEXT = re.compile(
    rf"\b(?:100\s*%|totalmente|integralmente|fully|completamente|entirely)\s*{REMOTE}\b"
    rf"|\b(?:vaga|trabalho|trabalhar|atuacao|posicao|local(?:izacao)?|modelo|modalidade|regime|formato|contratacao|workplace|location|role|position|work)\s*(?:de trabalho)?\s*[:=|-]?\s*(?:(?:e|is|a|em|no|de|modelo|formato|regime|modo|uma|vaga)\s+)*{REMOTE}\b"
    rf"|\b{REMOTE}\s+(?:position|role|job|work in|anywhere)\b"
    rf"|(?:^|[\n|•#])\s*[-*:# ]*{REMOTE}\s*(?=$|[\n|•(.,;!#])"
    rf"|(?:^|\n)\s*{REMOTE}\s*[-–(/]\s*(?:brasil|brazil|latam|mundial|worldwide|anywhere)\b"
    r"|\b(?:atuar|trabalhar|trabalhando)\s+(?:remotamente|de forma remota)\b"
    r"|\b(?:trabalhe de (?:casa|qualquer lugar)|work from anywhere)\b", re.M)
BR_REGION = re.compile(r"\b(?:bra[sz]il|br|bra|latam|latin america|america latina|south america|america do sul|americas|amer|worldwide|global|mundial|anywhere|qualquer lugar)\b")
FOREIGN = r"(?:united states|usa|us|u\.s\.?|eua|estados unidos|canada|united kingdom|uk|europe|europa|emea|apac|india|germany|portugal|australia)"
FOREIGN_ONLY = re.compile(rf"\b{FOREIGN}[- ]only\b|\b(?:only (?:in|within)|must (?:reside|live|be (?:located|based)) in|residir (?:em|nos?)|apenas (?:em|nos?|para residentes (?:em|nos?)))\s+(?:(?:the|os)\s+)?{FOREIGN}\b")


def workplace(job: JobDetails) -> tuple[Workplace, str]:
    """Sinais de presença obrigatória prevalecem inclusive sobre um selo remoto."""
    labels = f"{job.title}\n{job.location}\n{job.level}"
    body = re.sub(r"(?m)^\s*[-*]?\s*\[\s*\].*$", "", job.description)
    body = re.sub(r"(?im)^\s*[-*]?\s*\[x\]\s*", "", body)
    fields = TECHNICAL.sub("", plain(labels))
    text = TECHNICAL.sub("", plain(body))
    text = re.sub(rf"\b(?:processo seletivo|entrevistas?|interviews?|hiring process)\s*[:=-]?\s*(?:(?:e|is|sera|sao|serao|100\s*%|fully|totalmente)\s+)*{REMOTE}\b", "", text)
    combined = fields + "\n" + text
    if job.workplace in ("hybrid", "onsite"):
        return job.workplace, "Modalidade informada pelo portal"
    if match := NEGATION.search(combined):
        return "onsite", match[0]
    negative = re.search(rf"\b{OFFICE}\b", fields) or ONSITE.search(combined) or re.search(
        rf"(?m)^\s*[-*# ]*({OFFICE})\s*[.!]?\s*$", text)
    if negative:
        return "hybrid" if re.search("hybrid|hibrid", negative[0]) else "onsite", negative[0]
    if job.workplace == "remote":
        return "remote", "Campo de modalidade da fonte · remoto"
    for label, value in (("Título", job.title), ("Local", job.location), ("Classificação da fonte", job.level)):
        if match := re.search(rf"\b{REMOTE}\b", TECHNICAL.sub("", plain(value))):
            return "remote", f"{label} · {match[0]}"
    if match := REMOTE_TEXT.search(text):
        return "remote", "Descrição · " + match[0].strip()
    if job.is_remote and not (job.source == "linkedin" and TECHNICAL.search(plain(labels))):
        return "remote", "Remoto informado pelo portal"
    return "unknown", "Modalidade sem confirmação"


def available_from_brazil(job: JobDetails) -> bool:
    regions = plain(" / ".join(job.applicant_regions))
    if regions and not BR_REGION.search(regions):
        return False
    location = plain(job.location)
    if not BR_REGION.search(location) and re.search(rf"\b{FOREIGN}\b", location):
        return False
    text = plain(job.title + "\n" + job.description)
    if re.search(r"\b(?:exclud(?:es|ing)|except|exceto|menos)\s+(?:o\s+)?bra[sz]il\b", text + " " + location):
        return False
    designation = plain(job.title) + " " + location
    if not BR_REGION.search(designation) and re.search(rf"\bremote\s*[-(/|: ]+\s*{FOREIGN}\b", designation):
        return False
    return not FOREIGN_ONLY.search(text)
