import re
from functools import lru_cache
from pathlib import Path

from src.core.models import Evidence, plain

EVIDENCE_HEADING = "## Evidências por requisito"
WEIGHTS = {"Experiência": 0.7, "Ensino": 0.5, "Projeto": 0.5, "Certificação": 0.2, "Formação": 0.2}


@lru_cache(maxsize=1024)
def term_pattern(term: str) -> re.Pattern:
    pattern = re.escape(plain(term)).replace(r"\ ", r"[\s/_–-]+")
    return re.compile(r"(?<!\w)" + pattern + r"(?!\w)")


def has_term(text: str, term: str) -> bool:
    return bool(term_pattern(term).search(plain(text)))


def read_profile(path: Path) -> tuple[str, list[Evidence]]:
    """A tabela opcional integra o mesmo Markdown usado localmente e no Actions."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return "", []
    if "Modelo para uma instalação nova." in text:
        return "", []
    section = re.search(r"^" + re.escape(EVIDENCE_HEADING) + r"[ \t]*$", text, re.M)
    if not section:
        return text, []
    before, rest = text[:section.start()], text[section.end():]
    following = re.search(r"^#{1,2} ", rest, re.M)
    table = rest[:following.start()] if following else rest
    narrative = before + (rest[following.start():] if following else "")
    records = []
    for line in table.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] not in WEIGHTS:
            continue
        kind, fact, source, keywords = cells
        terms = list(dict.fromkeys(k.strip() for k in keywords.split(",") if k.strip()))
        if fact and source and terms:
            records.append(Evidence(kind=kind, fact=fact, source=source, terms=terms))
    return narrative.strip(), records


def relevant_evidence(text: str, records: list[Evidence]) -> list[Evidence]:
    matches = []
    folded = plain(text)
    for record in records:
        hits = [term for term in record.terms if term_pattern(term).search(folded)]
        if hits:
            matches.append(record.model_copy(update={"terms": hits}))
    matches.sort(key=lambda item: (len(item.terms), WEIGHTS[item.kind]), reverse=True)
    selected = []
    covered = set()
    for item in matches:
        keys = {plain(term) for term in item.terms}
        if keys - covered:
            selected.append(item)
            covered.update(keys)
        if len(selected) == 3:
            break
    return selected


def extra_credit(evidence: list[Evidence], stack_matches: list[str]) -> float:
    """Um adicional por evidência que introduz termos, sem repetir a stack ou outra fonte."""
    covered = {plain(term) for term in stack_matches}
    credit = 0.0
    for item in sorted(evidence, key=lambda item: WEIGHTS[item.kind], reverse=True):
        terms = {plain(term) for term in item.terms}
        if terms - covered:
            credit += WEIGHTS[item.kind]
            covered.update(terms)
    return min(credit, 1.4)
