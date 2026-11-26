import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlsplit

from src.core.models import plain

REDIRECT_HOSTS = {"www.linkedin.com", "linkedin.com", "www.google.com", "google.com", "l.facebook.com", "l.instagram.com"}


def google_form_url(value: str, *, _unwrap: bool = True) -> str | None:
    """Retém só o link público do formulário, sem dados pré-preenchidos."""
    try:
        url = urlsplit(unescape(value.strip()))
        if url.scheme != "https" or url.username or url.password or url.port is not None:
            return None
        host = (url.hostname or "").lower()
        if _unwrap and host in REDIRECT_HOSTS:
            params = parse_qs(url.query)
            for key in ("url", "q", "u"):
                for target in params.get(key, ()):
                    if resolved := google_form_url(unquote(target), _unwrap=False):
                        return resolved
            return None
        if host == "forms.gle" and re.fullmatch(r"/[A-Za-z0-9_-]+/?", url.path):
            return "https://forms.gle" + url.path.rstrip("/")
        path = (
            re.fullmatch(r"/forms/(?:u/\d+/)?d/((?:e/)?[\w-]+)/viewform/?", url.path)
            or re.fullmatch(r"/forms/(?:u/\d+/)?d/(e/[\w-]+)/?", url.path)
        )
        if host == "docs.google.com" and path:
            return f"https://docs.google.com/forms/d/{path[1]}/viewform"
    except ValueError:
        pass
    return None


LINK = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.I)
APPLY = re.compile(r"candidat|inscric|inscrev|appli(?:cation|que)|\bapply\b|processo seletivo|como participar|preench|envie (?:seu|sua|as)|(?:atraves|por meio|pelo|neste|no link) (?:des[st]e |do )?formulario|interessad[oa]s?|cadastre|cadastro|concorrer|seletiva|trabalhe conosco|link de inscricao|formulario da vaga")
UNRELATED = re.compile(r"newsletter|pesquisa de satisfacao|feedback|evento|webinar|sorteio|survey|banco de talentos|lista de espera|"
                       r"questionario(?! (?:de |do |para )?(?:candidatura|processo seletivo|recrutamento))|encuesta|mentoria|briefing")


def application_form(text: str) -> str | None:
    lowered = text.lower()
    if "forms.gle" not in lowered and "docs.google.com" not in lowered:
        return None
    candidates = set()
    standalone = set()
    for match in LINK.finditer(text):
        url = google_form_url(match[0].rstrip(".,;:!?*"))
        if not url:
            continue
        # O contexto local impede que o formulário do rodapé vire candidatura.
        before = text[max(0, match.start() - 180):match.start()].strip().splitlines()
        label = plain(" ".join(before[-2:]))
        after = plain(text[match.end():match.end() + 90].partition("\n")[0])
        if UNRELATED.search(label) or (re.match(r"\W*(?:para|da|de|for|our)\b", after) and UNRELATED.search(after)):
            continue
        if APPLY.search(label):
            candidates.add(url)
        elif (not before or not before[-1].strip(" 🔗👉:*-[(") or
              re.search(r"\b(?:link da vaga|formulario|google forms|inscricao|link para aplicar)\s*[:(]*$", label)):
            standalone.add(url)
        elif not text[text.rfind("\n", 0, match.start()) + 1:match.start()].strip(" 🔗👉:-*"):
            standalone.add(url)
    if not candidates and re.search(r"\b(vaga|oportunidade|hiring|contratando|requisitos|requirements)\b", plain(text)):
        candidates = standalone
    return candidates.pop() if len(candidates) == 1 else None
