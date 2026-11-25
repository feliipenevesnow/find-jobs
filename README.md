# Caçador de vagas júnior

Um monitor local para encontrar vagas de entrada em desenvolvimento de software. Ele consulta sete fontes, compara as oportunidades com seu perfil e guarda o histórico no SQLite.

## Ver suas vagas de manhã

No Windows, dê dois cliques em **Abrir vagas.cmd**. Ele abre o painel no navegador. Se ainda não houve coleta com alguma fonte disponível hoje, ele busca primeiro. O painel permite pesquisar por cargo, empresa ou tecnologia, ordenar por nota e abrir o anúncio original. O filtro inicial mostra vagas encontradas nos últimos sete dias.

Para ler o histórico imediatamente, inclusive offline, abra `data/painel.html`. O cabeçalho informa a hora da última busca, quantos portais responderam e eventuais erros. Uma página já aberta mostra os resultados novos ao ser recarregada.

Para instalar ou alterar a busca automática das **7h** no Agendador de Tarefas do Windows, execute na pasta do projeto:

```powershell
& ".\Agendar vagas.ps1" -Horario 07:00
```

O horário usa o relógio do Windows. A tarefa roda na sua sessão, com o computador ligado. Se perder o horário, o agendador tenta assim que a sessão estiver disponível. Abrir o painel pelo atalho também recupera a coleta do dia. Para fazer outra busca manual, execute `.\.venv\Scripts\python.exe main.py`. O painel e o banco ficam na pasta configurada para o histórico.

## Primeira busca no Windows

Abra o PowerShell na pasta do projeto. Use Python 3.11 ou superior.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[test]"
if (!(Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env-example -Destination .env }
.\.venv\Scripts\python.exe main.py --dry-run --hours 24 --skip-ai
```

Faça a cópia do modelo apenas na configuração inicial. O projeto inclui o mesmo modelo com o nome convencional `.env.example`. No Linux ou macOS, crie o ambiente com `python3 -m venv .venv`, use `.venv/bin/python` nos comandos e copie o modelo com `cp .env-example .env`.

O terminal mostra quantos anúncios cada fonte devolveu, quantas consultas falharam e quais oportunidades passaram pelo filtro, com nota e link. A prévia consulta a internet e trabalha com uma cópia em memória do histórico. Uma rodada com zero aprovadas é um resultado possível.

## Seu perfil

Edite `config.yaml` para ajustar a busca ao seu perfil.

| Campo | O que ajustar |
| --- | --- |
| `search_terms` | Buscas enviadas a cada portal. Termos mais específicos reduzem a quantidade de anúncios irrelevantes. |
| `roles`, `positive`, `blocked` | Cargos de interesse, indicadores de nível de entrada e termos que desclassificam a vaga. |
| `excluded_areas` | Expressões de outras áreas que podem aparecer na mesma busca, como “programação de TV”. |
| `stack`, `min_score` | Tecnologias e nota mínima. A nota local começa em 6,5, soma 0,7 por tecnologia e considera evidências adicionais, até 10 no total. Com IA, vale a nota do avaliador. |
| `remote`, `cities` | Aceitação de remoto e cidades para híbrido ou presencial. A lista vazia permite qualquer cidade nas fontes brasileiras. O LinkedIn e o Indeed exigem confirmação de localização no Brasil para vagas presenciais, ou uma cidade configurada. |
| `infojobs_cities` | Slugs de busca do InfoJobs. São Paulo é o exemplo inicial. |
| `hours`, `results_per_search` | Janela padrão de 26 horas e limite de resultados por busca. As duas horas de sobreposição ajudam a cobrir atrasos entre as manhãs. |
| `sources`, `github_repos` | Fontes ativas e repositórios de vagas com issues públicas. |
| `ai_budget` | Orçamento local compartilhado entre as buscas que usam o mesmo SQLite. Ajuste chamadas e tokens por 24 horas e tokens por minuto. |
| `resume`, `database` | Arquivo de currículo e caminho do SQLite, relativos à pasta do YAML. |

Gupy e GitHub fornecem horários de publicação. LinkedIn e Indeed recebem a janela nativa do JobSpy. InfoJobs e Vagas.com podem informar apenas o dia. O motor usa o início desse dia em São Paulo para decidir se ele cabe na janela. O Nube fornece um sitemap, e a primeira coleta persistente estabelece o inventário. Nas próximas, entram links novos de desenvolvimento quando a última leitura completa ocorreu dentro da janela. APinfo ficou fora da seleção após os testes de extração.

## Conferir o encaixe de uma vaga

O currículo local pode incluir a tabela **Evidências por requisito** do modelo `curriculo.md`. Cada linha liga um fato a uma fonte e a termos da vaga. O motor escolhe até três evidências, favorecendo as que cobrem mais termos. Experiência aplicada, ensino, projeto, formação e certificação aparecem identificados no resultado.

Cada evidência selecionada que acrescenta termos aos já pontuados pode contribuir uma vez. Os pesos são 0,7 para experiência, 0,5 para ensino ou projeto e 0,2 para formação ou certificação, até 1,4 ponto adicional. Os filtros de senioridade, experiência mínima e localização continuam valendo. A nota é uma regra de ordenação, sem interpretação probabilística.

Para avaliar um anúncio já encontrado, salve o título na primeira linha de um TXT e a descrição nas seguintes. Execute `main.py --avaliar-arquivo caminho/para/vaga.txt` com o Python do projeto. Esse comando trabalha localmente, mostra inclusive o motivo de um bloqueio e dispensa data de publicação. O histórico de coletas permanece reservado às consultas aos portais.

O painel mostra as evidências selecionadas no currículo. Quando a análise remota está habilitada, ela recebe um recorte do perfil e as evidências relevantes.

## Aderência por IA

Para dar nota às vagas, o modelo recomendado é o **Gemini Decision 1**. Ele classifica a aderência ao perfil, a senioridade e possíveis lacunas na mesma chamada, com zero tokens de saída. Em 25/11/2026, o Google publicava **10.000 chamadas por dia, 200 chamadas por minuto e 50.000 tokens por minuto** no plano gratuito. A disponibilidade efetiva depende do projeto. [Modelo e preços](https://ai.google.dev/gemini-api/docs/models/gemini-decision-1#pricing) · [Limites oficiais](https://ai.google.dev/gemini-api/docs/rate-limits#gemini-decision)

1. Abra o Google AI Studio e crie uma chave em um projeto que aparece como **Free**, sem faturamento vinculado. [Criar chave](https://aistudio.google.com/apikey) · [Consultar cotas da conta](https://aistudio.google.com/rate-limit)
2. No `.env`, use `AI_PROVIDER=gemini`, `AI_MODEL=gemini-decision-1` e cole a chave depois de `GEMINI_API_KEY=`. A busca funciona por regras enquanto a chave estiver vazia. Para usar sempre as regras, escolha `AI_PROVIDER=none`.
3. Revise `curriculo.local.md` antes da primeira análise remota. Use uma versão com competências e experiências descritas sem identificar pessoas ou clientes. O serviço gratuito do Google pode usar o conteúdo enviado para melhorar os produtos. [Termos do Gemini](https://ai.google.dev/gemini-api/terms)
4. Rode `.\.venv\Scripts\python.exe main.py --hours 24` para coletar e analisar as vagas. Consulte o consumo local a qualquer momento com `.\.venv\Scripts\python.exe main.py --uso-ia`.

**Como a cota é usada.** O caçador aplica as regras de cargo, nível e localização antes da IA. Cada chamada inclui até 2.600 caracteres da descrição, 1.050 do perfil e até três evidências. O Gemini recebe esses trechos uma vez para responder às perguntas sobre a vaga. O programa reutiliza a análise quando o conteúdo enviado e o modelo coincidem. Na prévia com `--dry-run`, ele consulta as análises remotas já salvas e usa as regras para vagas inéditas. [Como o Google contabiliza as perguntas](https://ai.google.dev/gemini-api/docs/decisions-api#multiple-questions)

O orçamento inicial do aplicativo é de **1.000 chamadas e 1.000.000 de tokens em 24 horas**, com **25.000 tokens por minuto**. Vale o primeiro limite atingido. O programa reserva uma estimativa antes da chamada e atualiza o total quando recebe o consumo do provedor. Se o serviço pedir uma pausa ou o orçamento diário acabar, as próximas vagas seguem pela avaliação local e recebem a marca `[Análise IA indisponível]`. O comando `--uso-ia` mostra esse histórico. O painel do Google inclui também o uso do mesmo projeto por outras instalações e aplicativos.

| `AI_PROVIDER` | Configuração no `.env` |
| --- | --- |
| `gemini` | O exemplo já traz `gemini-decision-1`. Também aceita modelos de geração do Gemini com saída JSON. [Chaves Gemini](https://ai.google.dev/gemini-api/docs/api-key) |
| `groq` | Preencha `GROQ_API_KEY` e escolha em `AI_MODEL` um modelo com modo JSON. [Console Groq](https://console.groq.com/keys) |
| `openai` | Preencha `OPENAI_API_KEY` e use em `AI_MODEL` um modelo compatível com Chat Completions e saída JSON. [Documentação da API](https://platform.openai.com/docs/api-reference/chat/create) |
| `ollama` | Inicie o Ollama, baixe um modelo com `ollama pull` e use o nome exibido em `ollama list` como `AI_MODEL`. O endereço local padrão é `http://127.0.0.1:11434`. [Documentação do Ollama](https://docs.ollama.com/api/chat) |

Use `--skip-ai` para aplicar apenas as regras nessa rodada.

## Histórico e candidaturas

```powershell
.\.venv\Scripts\python.exe main.py --list
.\.venv\Scripts\python.exe main.py --status 42 salva
.\.venv\Scripts\python.exe main.py --status 42 entrevista
```

Substitua `42` pelo ID exibido. Os estados disponíveis são `nova`, `salva`, `candidatado`, `entrevista` e `descartada`. A lista do terminal traz as últimas 50 oportunidades aprovadas. O painel permite consultar todo o histórico e mostra o estado de cada candidatura.

O SQLite reúne anúncios repetidos pela URL ou pela combinação de empresa e título. Uma vaga aprovada continua disponível ao envelhecer. Use `main.py --painel` para reconstruir a página a partir do banco.

## Testes e referências

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Os testes usam respostas controladas para validar falhas de fonte, leitura de anúncios, deduplicação, filtros, IA, leitura do painel local e persistência entre rodadas. A consulta real de diagnóstico é feita pela CLI com `--dry-run`.

Os parsers de InfoJobs e Vagas.com e os padrões de links do Nube vêm do **monitor-vagas**, com a licença MIT preservada em `licenses/monitor-vagas.txt`. [Código de origem](https://github.com/joaosousa51/monitor-vagas)

O **JobSpy** (MIT) fornece LinkedIn e Indeed. A rota da Gupy e a leitura da descrição foram verificadas a partir do **radar-de-vagas** (MIT). O **vagas-dev** (GPL-3.0) e o **gupy-jobs-scraper** (MIT) foram consultados na auditoria. As revisões e o resultado por portal estão no [relatório local](AUDITORIA.md). [JobSpy](https://github.com/speedyapply/JobSpy) · [radar-de-vagas](https://github.com/oliveirarenanfelipe/radar-de-vagas) · [vagas-dev](https://github.com/henriquesebastiao/vagas-dev) · [gupy-jobs-scraper](https://github.com/datadmon/gupy-jobs-scraper)
