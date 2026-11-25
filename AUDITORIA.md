# Auditoria dos coletores de vagas

**Decisão proposta:** montar um projeto Python modular e reaproveitar os coletores validados. O `monitor-vagas` concentra coleta, perfil, persistência e Telegram em um arquivo de mais de 6.800 linhas. Ao importá-lo, ele substitui o método de busca do LinkedIn. Essa alteração falhou com JobSpy 1.5.0. A mesma biblioteca, executada isoladamente, trouxe vagas.

## Consultas reais em 25/11/2026

Os testes usaram fontes públicas, sem tokens pessoais. As contagens medem a leitura das fontes. A triagem de senioridade, o cálculo de aderência e o envio ao Telegram fazem parte da implementação posterior.

| Fonte | Consulta e resultado | Uso proposto |
| --- | --- | --- |
| Gupy | A rota `employability-portal.gupy.io/api/v1/jobs` respondeu 404. A rota `portal.gupy.io/api/job-search/jobs` retornou três vagas com título, link e data para “desenvolvedor”. Foi possível ler a descrição completa da primeira vaga. | Usar a rota identificada no radar-de-vagas e validar a data em cada anúncio. |
| LinkedIn | Com a adaptação do monitor, a consulta falhou com `AttributeError` ao acessar `session`. Com JobSpy 1.5.0 isolado, a busca “desenvolvedor”, Brasil, com `hours_old=2` retornou três vagas com link. | Chamar o JobSpy diretamente. |
| Indeed | JobSpy retornou três vagas com link para a mesma busca e janela de duas horas. | Preservar o filtro de recência da fonte. |
| GitHub Issues | A API retornou issues abertas com data, título e link nos repositórios `backend-br/vagas` (3), `frontendbr/vagas` (3) e `pydevbr/vagas` (2). A amostra foi limitada às três mais recentes de cada repositório. | Usar a data de criação e excluir pull requests. |
| InfoJobs | O parser do monitor leu 20 anúncios da listagem de desenvolvedor em São Paulo. | Reaproveitar a extração listagem a listagem. |
| Vagas.com | O parser do monitor leu sete anúncios da listagem de desenvolvedor. | Reaproveitar o parser de listagens. |
| Nube | O parser reconheceu 3.024 links de estágio e aprendizagem no sitemap. A consulta de detalhe da primeira vaga retornou os campos de requisitos esperados. | Filtrar a área de desenvolvimento e tratar a primeira leitura como inventário. |
| APinfo | A consulta de desenvolvedor em SP respondeu HTTP 200, mas o parser extraiu zero vagas nas janelas de sete e 60 dias. O contador esperado também estava ausente na segunda consulta. | Manter desativado até validar com uma vaga conhecida. |

Fontes que informam somente o dia ou a primeira observação exigem tratamento próprio. A janela de duas horas deve usar um horário de publicação verificável ou o filtro nativo do portal. O painel deve identificar quando a única informação disponível é a data de descoberta.

## Referências e licenças conferidas

| Projeto e revisão | Licença | Contribuição |
| --- | --- | --- |
| [monitor-vagas](https://github.com/joaosousa51/monitor-vagas/tree/965ebfd) | MIT | Parsers de portais brasileiros, SQLite, fila de mensagens e acompanhamento de candidaturas. |
| [JobSpy](https://github.com/speedyapply/JobSpy/tree/c1be997) | MIT | Biblioteca instalada do PyPI para os testes com LinkedIn e Indeed. |
| [radar-de-vagas](https://github.com/oliveirarenanfelipe/radar-de-vagas/tree/da2d3f0) | MIT | Rota atual da Gupy e triagem que registra motivos recorrentes de descarte como pontos de atenção. |
| [vagas-dev](https://github.com/henriquesebastiao/vagas-dev/tree/d5560c4) | GPL-3.0 | Referência de paginação, modalidades e níveis. A rota Gupy desta revisão retornou 404. |
| [gupy-jobs-scraper](https://github.com/datadmon/gupy-jobs-scraper/tree/cfeb747) | MIT | O cliente chama um serviço hospedado no Apify e exige token. A rota pública validada atende ao projeto sem esse serviço. |

## Estrutura proposta para a fase 1

```text
main.py                       CLI com --dry-run, --hours e --skip-ai
config.yaml                   Cargos, stack, local, fontes, recência e nota mínima
curriculo.md                  Modelo editável usado na análise de aderência
.env-example                  Credenciais e seleção do provedor de IA
src/
  collectors/                 JobSpy, Gupy, GitHub e portais com coleta validada
  core/                       Configuração, modelo da vaga, URLs e deduplicação
  filters/                    Regras, pontuação e análise opcional do currículo
  storage/                    SQLite e histórico de alertas e candidaturas
  notifiers/                  Telegram e registro de entregas
tests/                        Coletores, deduplicação, filtros e respostas da IA
.github/workflows/hunt.yml    Execução horária e manual
pyproject.toml                Dependências do projeto e dos testes
README.md                     Instalação, configuração e primeiro teste
```

Dependências previstas para Python 3.11 ou superior: `python-jobspy`, `httpx`, `beautifulsoup4`, `pydantic`, `PyYAML`, `python-dotenv` e `pytest` para os testes. SQLite faz parte do Python. A IA começa desligada e o primeiro teste pode rodar sem credenciais. O painel responsivo em FastAPI + React/Vite lerá esse mesmo banco na fase 2, após a confirmação do primeiro alerta.
