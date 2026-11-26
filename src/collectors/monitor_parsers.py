"""Parsers de joaosousa51/monitor-vagas (965ebfd), sob licença MIT.
Consulte licenses/monitor-vagas.txt. Adaptados para nível, modalidade e datas brasileiras.
"""
import html
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from src.collectors.base import BR, content
from src.core.forms import application_form


INFOJOBS_BASE = "https://www.infojobs.com.br"


_URL_VAGA = re.compile(r"/vaga-de-[^/?#]*?__(\d+)\.aspx", re.I)


_MESES_IJ = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
          "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def _sem_acento(txt):
    return "".join(c for c in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(c) != "Mn")


def _norm(txt):
    return re.sub(r"\s+", " ", _sem_acento(txt or "").lower()).strip()


def _limpar(txt):
    return re.sub(r"\s+", " ", html.unescape(txt or "")).strip()


def _parse_data(texto):
    """'Hoje' / 'Ontem' / '14 ago' / '03/07/26' -> date, ou None.

    LEIA O _data_do_bloco() ABAIXO ANTES DE CHAMAR ISTO COM TEXTO DE CARD.

    Esta função busca no texto INTEIRO que recebe, e é por isso que ela não
    pode receber o card todo: o "hoje" dela casa tanto com o rótulo de data
    do portal quanto com "venha fazer parte hoje mesmo" no meio do anúncio.
    Ela é o parser de UM rótulo. Quem acha o rótulo é o _data_do_bloco().

    _MESES_IJ nas DUAS pontas desde 19/08/2026. O guard consultava _MESES_IJ e
    o lookup lia _MESES — resquício da fusão descrita no cabeçalho do arquivo,
    metade renomeada. Inofensivo enquanto as doze abreviações coincidirem, e
    KeyError silencioso no dia em que alguém puser uma chave nova só num deles.
    """
    if not texto:
        return None
    t = _norm(texto)

    if "hoje" in t:
        return datetime.now(BR).date()
    if "ontem" in t:
        return datetime.now(BR).date() - timedelta(days=1)

    m = re.search(r"\b(\d{1,2})\s+([a-z]{3})", t)
    if m and _MESES_IJ.get(m.group(2)):
        dia, mes, hoje = int(m.group(1)), _MESES_IJ[m.group(2)], datetime.now(BR).date()
        try:
            d = date(hoje.year, mes, dia)
        except ValueError:
            return None
        # "28 dez" lido em janeiro é do ano passado. Sem isto a data vem no
        # futuro e o MAX_DIAS trata a vaga como recém-postada pra sempre.
        if d > hoje + timedelta(days=1):
            try:
                d = date(hoje.year - 1, mes, dia)
            except ValueError:
                return None
        return d

    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", t)
    if m:
        dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ano += 2000 if ano < 100 else 0
        try:
            return date(ano, mes, dia)
        except ValueError:
            return None
    return None


_SO_DATA = re.compile(
    r"^\s*(?:"
    r"hoje|ontem"
    r"|h[áa]\s+\d+\s+(?:dia|dias|hora|horas)"
    r"|\d{1,2}\s+[a-z]{3}\.?"
    r"|\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}"
    r")\s*\.?\s*$", re.I)


_DATA_ABSOLUTA = re.compile(
    r"(h[áa]\s+\d+\s+dias?"
    r"|\b\d{1,2}\s+(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b)", re.I)


def _data_do_bloco(bloco, texto_cru=""):
    """Data de publicação de um card. Nó primeiro, formato absoluto depois.

    `bloco` é um nó do BeautifulSoup; `texto_cru` é o texto já concatenado,
    aceito só para o passe de segurança (quem chama normalmente já o tem em
    mão e não precisa remontar).

    Devolve `date` ou None. None aqui significa "não achei rótulo de data",
    e é tratado pelo VAGASCOM_EXIGIR_DATA / INFOJOBS_EXIGIR_DATA de quem
    chama — que já contam e logam essas ocorrências como "sem data".
    """
    if bloco is not None and hasattr(bloco, "stripped_strings"):
        for pedaco in bloco.stripped_strings:
            if _SO_DATA.match(pedaco):
                achado = _texto_para_data(pedaco)
                if achado:
                    return achado

    # Rede de segurança: markup mudou, nenhum nó isolado casou. Só absoluto.
    alvo = texto_cru or (bloco.get_text(" ") if bloco is not None else "")
    m = _DATA_ABSOLUTA.search(alvo)
    if m:
        return _texto_para_data(m.group(0))
    return None


def _texto_para_data(rotulo):
    """Um rótulo JÁ ISOLADO -> date. Cobre 'Há N dias' além do _parse_data()."""
    m = re.search(r"h[áa]\s+(\d+)\s+dias?", rotulo, re.I)
    if m:
        return datetime.now(BR).date() - timedelta(days=int(m.group(1)))
    m = re.search(r"h[áa]\s+(\d+)\s+horas?", rotulo, re.I)
    if m:
        return datetime.now(BR) - timedelta(hours=int(m.group(1)))
    return _parse_data(rotulo)


def _traduzir_requisitos(bloco):
    """Converte os campos ESTRUTURADOS do InfoJobs para o vocabulário dos seus
    regex. Continua sendo o que essa fonte tem de mais valioso.

    O site escreve experiência como rótulo fechado: "Sem experiência",
    "Menos de 1 ano", "Entre 1 e 3 anos", "Entre 3 e 5 anos", "Mais de 10 anos".

    - "Sem experiência" já casa com o ENTRADA_NA_DESCRICAO. Passa direto.
    - "Entre 3 e 5 anos" NÃO casa com o EXPERIENCIA_EXIGIDA, que espera
      "\\d+ anos de experiência". Sem tradução o corta-fora ficaria cego justo
      na fonte que entrega o dado pronto. Vira "experiência mínima de 3 anos".
    """
    achados = []
    t = _norm(bloco)

    if "sem experiencia" in t:
        achados.append("sem experiência")
    elif "menos de 1 ano" in t:
        achados.append("primeira experiência")
    else:
        m = re.search(r"entre\s+(\d+)\s+e\s+\d+\s+anos", t) or \
            re.search(r"mais de\s+(\d+)\s+anos", t)
        if m:
            n = int(m.group(1))
            # "1 anos" ficava feio e ainda casava mal com regex que espera
            # a forma singular.
            achados.append(f"experiência mínima de {n} "
                           f"{'ano' if n == 1 else 'anos'}")

    if "cursando" in t:
        achados.append("cursando ensino superior")

    # PcD. ACRESCENTADO em 19/08/2026, e é o furo mais caro que esta fonte
    # tinha: o InfoJobs marca vaga afirmativa num CAMPO do card, escrevendo só
    # "PcD" seco, e o _vaga_exclusiva() exige um marcador de restrição
    # colado ao grupo quando o sinal vem da descrição.
    #
    # Essa regra está certa e não deve mudar — na prosa, "PcD" solto aparece o
    # tempo todo em vaga ABERTA ("pessoas com deficiência são bem-vindas"), e
    # cortar por marcador solto descartaria metade das vagas do Brasil. O que
    # estava errado era entregar um CAMPO estruturado disfarçado de prosa.
    #
    # Medido: 'Analista De Suporte - Moema SP' (TECNOCOMP), com o campo PcD no
    # card, devolvia None no _vaga_exclusiva() e só morria por outro motivo.
    #
    # Aqui o campo vira a frase que o MARCADOR_EXCLUSIVIDADE já sabe casar —
    # mesma tática da tradução de experiência logo acima. A palavra tem que
    # estar isolada: \bpcd\b não casa dentro de outra coisa, e o _norm() já
    # tirou acento e caixa.
    if re.search(r"\bpcd\b|pessoa[s]?\s+com\s+defici", t):
        achados.append("vaga exclusiva para PcD")

    return " · ".join(achados)


_NAO_EMPRESA = re.compile(
    r"^(?:https?://(?:www\.)?infojobs\.com\.br)?"
    r"/(?:vaga-de-|vagas-|empregos-|legal/|ajuda/|cargo|salarios|"
    r"ranking-|Candidate|company/|blog|por-que-|anunciar-)", re.I)


_SUBDOMINIOS_FORA = {"blog", "login", "supportcenter", "ncdn", "infojobs"}


def _eh_link_empresa(href):
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return False

    if href.startswith("http"):
        # Checa o HOST, não substring. A primeira versão testava
        # `"infojobs.com.br" not in href` e aceitava
        # https://www.facebook.com/infojobs.com.br/ — o domínio deles aparece
        # no CAMINHO do link da rede social. Um card com esse link viraria
        # empresa chamada "Facebook Infojobs" gravada no banco.
        host = urlsplit(href).netloc.lower()
        if not (host == "infojobs.com.br" or host.endswith(".infojobs.com.br")):
            return False
        sub = host.split(".")[0]
        if sub in _SUBDOMINIOS_FORA and host != "www.infojobs.com.br":
            return False

    return not _NAO_EMPRESA.match(href)


def _empresas_em_ordem(sopa):
    """Casa cada vaga com a empresa varrendo o documento UMA vez, em ordem.

    A v1 subia na árvore a partir do link da vaga. Falhava trazendo a empresa
    do card SEGUINTE quando o card atual não tinha link no formato esperado —
    e atribuição errada de empresa é veneno aqui, porque a chave
    normalizada (título+empresa) grava isso como verdade por 7 dias.

    Ordem de documento não depende de aninhamento, que é justo a parte que
    muda quando o site mexe no layout.
    """
    empresa_de, atual = {}, None
    for a in sopa.find_all("a", href=True):
        m = _URL_VAGA.search(a["href"])
        if m:
            atual = m.group(1)
            continue
        if atual is None or atual in empresa_de:
            continue
        if not _eh_link_empresa(a["href"]):
            continue
        nome = _limpar(a.get_text(" "))
        if 2 < len(nome) < 120:
            empresa_de[atual] = nome
    return empresa_de


def _extrair_jsonld(sopa):
    """Nível 1: JSON-LD JobPosting. No teste real do InfoJobs não existe, mas
    fica: custa uma varredura e é a estrutura que não quebra com CSS."""
    vagas = []
    for tag in sopa.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            dados = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        fila = dados if isinstance(dados, list) else [dados]
        expandido = []
        for c in fila:
            if isinstance(c, dict) and isinstance(c.get("@graph"), list):
                expandido.extend(c["@graph"])
            else:
                expandido.append(c)

        for item in expandido:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            url = _limpar(item.get("url") or "")
            m = _URL_VAGA.search(url)
            if not m:
                continue

            local, jl = "", item.get("jobLocation")
            if isinstance(jl, list) and jl:
                jl = jl[0]
            if isinstance(jl, dict) and isinstance(jl.get("address"), dict):
                end = jl["address"]
                local = f"{_limpar(end.get('addressLocality'))}, " \
                        f"{_limpar(end.get('addressRegion'))}".strip(" ,")

            org = item.get("hiringOrganization") or {}
            publicada = None
            if item.get("datePosted"):
                try:
                    publicada = datetime.fromisoformat(
                        str(item["datePosted"]).replace("Z", "+00:00")).date()
                except ValueError:
                    publicada = _parse_data(str(item["datePosted"]))

            vagas.append({
                "id": f"ij-{m.group(1)}",
                "site": "infojobs",
                "title": _limpar(item.get("title")),
                "company": _limpar(org.get("name") if isinstance(org, dict) else ""),
                "location": local,
                "job_url": url if url.startswith("http") else INFOJOBS_BASE + url,
                "remote": item.get("jobLocationType") == "TELECOMMUTE",
                "date_posted": publicada.strftime("%Y-%m-%d") if publicada else "",
                "description": content(item.get("description")),
                "_via": "json-ld",
            })
    return vagas


def _extrair_cards(sopa, cidade_rotulo):
    """Nível 2: âncora da vaga + subida limitada. Não usa classe CSS nenhuma."""
    vagas = {}
    empresa_por_vaga = _empresas_em_ordem(sopa)

    for a in sopa.find_all("a", href=True):
        m = _URL_VAGA.search(a["href"])
        if not m or m.group(1) in vagas:
            continue
        vid = m.group(1)

        titulo = _limpar(a.get_text(" "))
        if len(titulo) < 3:
            continue

        # `no_bloco` guarda o NÓ, não só o texto: o _data_do_bloco() precisa
        # varrer os filhos um a um para achar o rótulo de data isolado. Antes
        # só o texto concatenado sobrevivia a este laço, e era essa perda que
        # obrigava a data a ser garimpada por substring lá embaixo.
        bloco, no, no_bloco = "", a, a
        for _ in range(5):
            no = no.parent
            if no is None:
                break
            texto = _limpar(no.get_text(" "))
            if len(texto) > 4000:      # subiu demais, pegou a listagem inteira
                break
            bloco, no_bloco = texto, no
            t = _norm(texto)
            if re.search(r"\b(hoje|ontem|\d{1,2}\s+[a-z]{3})\b", t) and \
               re.search(r"experi|ensino|presencial|hibrido|home office", t):
                break

        requisitos = _traduzir_requisitos(bloco)
        # Prefixa: o LIMITE_DESCRICAO_ZONA corta em 600 caracteres, então
        # o requisito traduzido precisa vir na frente pra sobreviver.
        descricao = f"{requisitos}. {bloco}" if requisitos else bloco

        # "Empresa confidencial" NÃO é bug de extração — é o que o InfoJobs
        # escreve quando o anunciante esconde o nome, e aparece como TEXTO,
        # não como link. Sem esta linha o campo vem vazio e você fica olhando
        # pro "(VAZIA)" achando que o parser falhou. Confirmado no teste real:
        # as 3 vazias de 13 eram todas confidenciais.
        #
        # Ressalva que vale conhecer: a chave normalizada é título+empresa,
        # então duas vagas confidenciais com o mesmo título colapsam na mesma
        # chave. Na prática não morde, porque o dedupe primário é o id
        # "ij-<numero>", que é único, e o CHAVE_TTL_DIAS já expira em 7 dias.
        empresa = empresa_por_vaga.get(vid, "")
        if not empresa and "empresa confidencial" in _norm(bloco):
            empresa = "Empresa confidencial"

        # Nó primeiro, texto concatenado só como rede. Ver _data_do_bloco().
        publicada = _data_do_bloco(no_bloco, bloco)

        vagas[vid] = {
            "id": f"ij-{vid}",
            "site": "infojobs",
            "title": titulo,
            "company": empresa,
            "location": cidade_rotulo,
            "job_url": (INFOJOBS_BASE + a["href"].split("?")[0]
                        if a["href"].startswith("/") else a["href"]),
            "date_posted": publicada.isoformat() if publicada else "",
            "description": descricao,
            "remote": any(_norm(part) in {"home office", "100% home office", "remoto", "100% remoto"}
                          for part in no_bloco.stripped_strings),
            "form_url": application_form(content(str(no_bloco))) if "forms." in str(no_bloco) or "google.com/forms" in str(no_bloco) else None,
            "_via": "html",
        }
    return list(vagas.values())


VAGASCOM_BASE = "https://www.vagas.com.br"


_VC_URL_VAGA = re.compile(r"/vagas/v(\d+)/")


_VC_LOCAL = re.compile(
    r"^([\wÀ-ÿ'´`\.\-]+(?: [\wÀ-ÿ'´`\.\-]+){0,4})\s*/\s*([A-Z]{2})$")


_VC_NIVEIS = ("Auxiliar/Operacional", "Estágio", "Júnior/Trainee", "Pleno",
              "Sênior", "Técnico", "Supervisão/Coordenação", "Gerência",
              "Diretoria")


_VC_MODELOS = ("100% Home Office", "Na empresa e Home Office", "Na empresa")


def _vc_container(link):
    """Sobe do <a> até o <li>/<article> que embrulha o card."""
    no = link
    for _ in range(6):
        no = no.parent
        if no is None:
            break
        if no.name in ("li", "article"):
            return no
    return link.parent or link


def _vc_empresa(bloco, titulo):
    """Nome do anunciante.

    Caminho limpo: o alt do logo, que o portal escreve como
    "Logo da empresa Huawei". Fallback: o texto entre o fim do título e o
    marcador de nível, que é onde a empresa fica quando não há logo.

    Vaga sem nome de anunciante existe e é normal aqui — o portal mostra
    "Confidencial" como TEXTO. Cai no fallback e sai correto.
    """
    img = bloco.find("img", alt=re.compile(r"^Logo da empresa\s+", re.I))
    if img and img.get("alt"):
        return _limpar(re.sub(r"^Logo da empresa\s+", "", img["alt"], flags=re.I))

    texto = _limpar(bloco.get_text(" "))
    if titulo and titulo in texto:
        depois = texto.split(titulo, 1)[-1].strip()
        nivel = _vc_nivel(depois)
        if nivel:
            return _limpar(depois.split(nivel, 1)[0])[:80]
        return _limpar(depois)[:80]
    return ""


def _vc_nivel(texto):
    """Marcador de nível que aparece PRIMEIRO no texto, ou "".

    CORRIGIDO em 19/08/2026. Era `next(x for x in _VC_NIVEIS if x in texto)`,
    que devolve o primeiro item da TUPLA presente no texto — e não o primeiro
    que aparece NO texto. Como "Estágio" é o segundo item da tupla, uma vaga
    Pleno cuja descrição mencionasse estágio era cortada ali:

        company = 'Confidencial Pleno Liderar tecnicamente a equipe de TI.'

    Isso vazava para o _chave_dedupe(titulo, empresa), então a MESMA vaga com
    um snippet ligeiramente diferente virava chave diferente e realertava —
    silenciosamente, porque nada nisso dá erro.

    "Técnico" e "Estágio" são os dois que mais causam o problema, justamente
    por serem palavras comuns dentro de título e de descrição.
    """
    achados = [(texto.find(x), x) for x in _VC_NIVEIS if x in texto]
    return min(achados)[1] if achados else ""


def _vc_extrair_cards(html_texto):
    """HTML da listagem -> lista de dicts no schema do JobSpy."""
    sopa = BeautifulSoup(html_texto, "html.parser")
    achadas = {}

    for a in sopa.find_all("a", href=True):
        m = _VC_URL_VAGA.search(a["href"])
        if not m or m.group(1) in achadas:
            continue
        vid = m.group(1)

        titulo = _limpar(a.get("title") or a.get_text(" "))
        if len(titulo) < 3:
            continue

        bloco = _vc_container(a)
        texto = _limpar(bloco.get_text(" "))

        cidade = uf = ""
        for pedaco in bloco.stripped_strings:
            loc = _VC_LOCAL.match(_limpar(pedaco))
            if loc:
                cidade, uf = _limpar(loc.group(1)), loc.group(2)
                break

        modelo = next((x for x in _VC_MODELOS if x in texto), "")
        if not cidade and modelo == "100% Home Office":
            cidade = "Remoto"

        # "São Paulo, SP" e não "São Paulo / SP": é o formato que o seu
        # LOCAIS_ACEITOS e o detectar_zona() já sabem ler.
        local = f"{cidade}, {uf}" if uf else cidade

        # Menor posição NO TEXTO, e não primeiro item da tupla. Ver o mesmo
        # conserto, e o mesmo motivo, no _vc_empresa().
        nivel = _vc_nivel(texto)
        # Nó primeiro; `texto` entra só como rede de segurança para formato
        # absoluto. Ver o cabeçalho do _data_do_bloco().
        d = _data_do_bloco(bloco, texto)

        # Resumo do card: sobra do texto depois de tirar o que já virou campo.
        # Serve de descrição provisória quando VAGASCOM_BUSCAR_DESCRICAO=False.
        resumo = texto
        for corte in (titulo, nivel, modelo, local, f"{cidade} / {uf}"):
            if corte:
                resumo = resumo.replace(corte, " ")
        resumo = re.sub(r"A empresa aceita candidaturas.*", "", resumo)

        href = a["href"].split("?")[0]
        achadas[vid] = {
            "id": f"vc-{vid}",
            "site": "vagascom",
            "title": titulo,
            "company": _vc_empresa(bloco, titulo),
            "location": local,
            "job_url": (VAGASCOM_BASE + href) if href.startswith("/") else href,
            "date_posted": d.isoformat() if d else "",
            "description": _limpar(resumo)[:800],
            "level": nivel,
            "remote": modelo == "100% Home Office",
            "workplace": {"Na empresa e Home Office": "hybrid", "Na empresa": "onsite",
                          "100% Home Office": "remote"}.get(modelo, "unknown"),
            "form_url": application_form(content(str(bloco))) if "forms." in str(bloco) or "google.com/forms" in str(bloco) else None,
        }

    return list(achadas.values())


_NUBE_URL = re.compile(
    r"/detalhes-vaga/(\d+)/vaga-de-(estagio|aprendiz)-em-(.+?)$", re.I)


_NUBE_CAUDA = re.compile(
    r"^(?P<meio>.+)-(?P<uf>[a-z]{2})-(?:bolsa|salario)-de-"
    r"(?P<valor>\d+)-mais-beneficios$", re.I)
