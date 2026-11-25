import base64
import hashlib
import json
import re
import unicodedata
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AwareDatetime, BaseModel, Field, computed_field, field_validator

Status = Literal["nova", "salva", "candidatado", "entrevista", "descartada"]
Precision = Literal["timestamp", "day", "native", "discovery", "unknown"]


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", plain(text)))


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Link de candidatura inválido")
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in
             {"refid", "trackingid", "trk", "trkind", "jobboardsource", "fromjk", "from", "src"}]
    if parts.hostname.endswith(".gupy.io") and path.startswith("/job/"):
        try:
            token = path.split("/")[-1]
            data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
            if isinstance(data, dict) and str(data.get("jobId", "")).isdigit():
                path = "/jobs/" + str(data["jobId"])
        except (ValueError, UnicodeError):
            pass
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(sorted(query)), ""))


class JobDetails(BaseModel):
    title: str = Field(min_length=1)
    location: str = ""
    is_remote: bool = False
    source: str
    description: str = ""
    level: str = ""


class JobPosting(JobDetails):
    company: str = ""
    url: str
    published_at: AwareDatetime | None = None
    precision: Precision = "unknown"
    salary: str = ""
    status: Status = "nova"

    @field_validator("url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        return canonical_url(value)

    @computed_field
    @property
    def id_hash(self) -> str:
        company = normalized(self.company)
        unknown = {"", "empresa confidencial", "confidencial", "confidential", "nao informada"}
        identity = self.url if company in unknown else company + "|" + normalized(self.title)
        return hashlib.sha256(identity.encode()).hexdigest()


class Analysis(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    score: float = Field(ge=0, le=10, allow_inf_nan=False)
    senioridade_real: Literal["estagio", "junior", "pleno", "senior", "indefinido"]
    skills_match: list[str] = Field(max_length=50)
    skills_faltantes: list[str] = Field(max_length=50)
    red_flags: list[str] = Field(max_length=20)
    resumo_curto: str = Field(min_length=1, max_length=600)

    @field_validator("resumo_curto")
    @classmethod
    def two_sentences(cls, value: str) -> str:
        if len(re.split(r"(?<=[.!?])\s+(?=\S)", value.strip())) > 2:
            raise ValueError("O resumo deve ter até duas frases.")
        return value.strip()


class Evidence(BaseModel):
    kind: Literal["Experiência", "Ensino", "Projeto", "Formação", "Certificação"]
    fact: str
    source: str
    terms: list[str]


class Decision(BaseModel):
    eligible: bool
    reason: str
    score: float = 0
    skills: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    analysis: Analysis | None = None
    ai_unavailable: bool = False
