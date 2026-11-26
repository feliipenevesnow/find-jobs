# Caçador de vagas júnior

Um monitor local de vagas de entrada em desenvolvimento de software para quem trabalha remotamente do Brasil. Ele cruza anúncios públicos com o perfil, dá prioridade a candidaturas por Google Forms e guarda o histórico no SQLite.

## Ver suas vagas de manhã

No Windows, dê dois cliques em **Abrir vagas.cmd**. Digite quantas horas deseja buscar, de 1 a 720. Por exemplo, `168` busca os últimos sete dias. Um período explícito inicia uma nova coleta mesmo que outra já tenha terminado hoje. Pressionar **Enter** usa a rotina diária, que reaproveita a coleta do dia quando alguma fonte respondeu.

No painel servido por **iniciar_servidor.bat**, abra `http://localhost:8090`, escolha o **Período da busca** e clique em **Buscar vagas agora**. **Personalizado** aceita um número de horas. No terminal, o equivalente para sete dias é `.\.venv\Scripts\python.exe main.py --hours 168`.

Após uma busca com período escolhido, o painel abre em **Última busca**. Em **Exibir vagas**, escolha **Todo o histórico salvo** para ver as aprovações anteriores. O filtro **Inscrição → Google Forms** mostra as vagas com esse canal de candidatura. O título de cada vaga abre o anúncio original.

Para ler o histórico imediatamente, inclusive offline, abra `data/painel.html`. O cabeçalho informa a hora da última busca, quantos portais responderam e eventuais erros. Uma página já aberta mostra os resultados novos ao ser recarregada.

Para configurar as buscas automáticas da manhã e da noite no Agendador de Tarefas do Windows, execute na pasta do projeto.

```powershell
& ".\Agendar vagas.ps1" -HorarioManha 08:00 -HorarioNoite 18:00
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

O terminal e a seção **Fontes da última busca** do painel mostram leituras, aprovações e formulários por fonte. Um anúncio pode voltar em mais de uma consulta. As aprovações por fonte contam vagas distintas dentro daquela fonte. A prévia consulta a internet e trabalha com uma cópia em memória do histórico.

## Onde o buscador procura

| Origem | Como a busca funciona |
| --- | --- |
| Carreiras de empresas | A Amazon é consultada no site oficial de carreiras, com filtro para o Brasil. Greenhouse e Ashby fornecem os murais de empresas configuradas, como Canonical, Cloudflare, GitLab, Grupo QuintoAndar, VTEX, Sierra Studio e Supabase. A lista de empresas pode ser ampliada no YAML. |
| Portais de vagas | Gupy, LinkedIn, Indeed, InfoJobs, Vagas.com e ProgramaThor cobrem anúncios nacionais. Himalayas pesquisa vagas de entrada abertas ao Brasil. Os feeds de programação de We Work Remotely trazem anúncios com as regiões aceitas. |
| Comunidades | O buscador lê publicações de canais brasileiros de Telegram e comentários de empregadores em *Who is hiring?* do Hacker News. Também pesquisa inscrições com Forms em issues públicas além dos repositórios de vagas configurados. |
| Web aberta | Consultas em português e inglês procuram formulários, carreiras e anúncios de empresas menores. O índice do Yahoo Brasil localiza páginas e o RSS do Bing assume em caso de bloqueio. Uma chave opcional em `BRAVE_SEARCH_API_KEY` seleciona a API de busca do Brave. A triagem usa o conteúdo da página de destino. |

No Ashby, o campo `workplaceType` distingue remoto integral de híbrido. No Greenhouse, a data de publicação é `first_published`. No Himalayas, a data do feed identifica uma atualização e aparece assim no painel. [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api) · [Greenhouse](https://docs.greenhouse.io/job-board) · [Himalayas](https://himalayas.app/docs/remote-jobs-api) · [We Work Remotely](https://weworkremotely.com/remote-job-rss-feed)

Nas páginas de empresas, o buscador lê os dados estruturados de vaga, incluindo modalidade, regiões aceitas e validade quando informadas. Nas demais páginas, procura um formulário ligado a uma oportunidade individual ou um anúncio datado com botão de candidatura. Páginas privadas, fora dos índices ou que exigem login podem limitar a cobertura. [Dados estruturados de vagas](https://developers.google.com/search/docs/appearance/structured-data/job-posting)

## Como os formulários ganham prioridade

O coletor procura links de candidatura `forms.gle` e `docs.google.com/forms` em anúncios, comunidades e índices da web. O Google permite que o organizador publique um link de resposta e defina quem pode acessá-lo. A API oficial de Forms exige um ID e autorização para ler um formulário. A descoberta do buscador depende dos links divulgados publicamente. [Publicação e acesso ao formulário](https://support.google.com/docs/answer/2839588?hl=pt-BR) · [Consulta pela API do Google Forms](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/get)

O título e a apresentação do formulário podem completar o cargo, o nível e a modalidade de um anúncio curto. Uma indicação explícita de pleno, sênior ou presencial pode tirar a vaga da seleção mesmo que o anúncio tenha a etiqueta júnior ou remoto. As perguntas sobre preferências do candidato ficam fora dessa avaliação. Para verificar um formulário recebido por mensagem, substitua `LINK` pelo link de resposta. O comando faz a leitura pública e mostra a avaliação local.

```powershell
.\.venv\Scripts\python.exe main.py --avaliar-formulario "LINK"
```

Um período escolhido vale também para Forms. Na busca padrão e nos alarmes, um formulário confirmado como aberto permite recuperar anúncios datados dos últimos **30 dias**. Um formulário sem data de publicação entra pela primeira observação no banco e recebe a indicação **Publicação sem data informada**. Para uma vaga datada dentro da janela, se o formulário pedir login ou estiver indisponível, o link aparece como **indicado no anúncio** quando o próprio anúncio atende ao perfil. Formulários recentes são reverificados. Uma inscrição encerrada sai da seleção quando era a única via de candidatura.

## Seu perfil

Edite `config.yaml` para ajustar a busca ao seu perfil.

| Campo | O que ajustar |
| --- | --- |
| `search_terms` | Buscas enviadas a cada portal. Termos mais específicos reduzem a quantidade de anúncios irrelevantes. |
| `roles`, `positive`, `blocked` | Cargos de interesse, indicadores de nível de entrada e termos que desclassificam a vaga. |
| `excluded_areas` | Expressões de outras áreas que podem aparecer na mesma busca, como “programação de TV”. |
| `stack`, `min_score` | Tecnologias e nota mínima. A nota local começa em 6,5, soma 0,7 por tecnologia e considera evidências adicionais, até 10 no total. Essa nota controla a seleção mesmo com IA habilitada. |
| `remote`, `cities` | Com `remote: true`, as regras exigem evidência de remoto integral e verificam restrições geográficas declaradas. Presença obrigatória prevalece sobre um selo remoto. `cities` é usado quando `remote: false`. |
| `infojobs_cities` | Slugs de busca do InfoJobs. São Paulo é o exemplo inicial. |
| `hours`, `results_per_search`, `forms_lookback_days` | A busca manual padrão cobre 36 horas, com até 30 resultados por consulta. A janela ampliada para Forms se aplica à rotina padrão e aos alarmes. |
| `sources`, `github_repos` | Fontes ativas e repositórios de vagas com issues públicas. |
| `greenhouse_boards`, `ashby_boards` | Mapas entre o identificador público do mural e o nome da empresa. |
| `community_channels`, `web_queries`, `web_pages_per_query` | Canais públicos do Telegram, pesquisas na web e número máximo de páginas originais abertas por pesquisa. |
| `ai_budget` | Orçamento local compartilhado entre as buscas que usam o mesmo SQLite. Ajuste chamadas e tokens por 24 horas e tokens por minuto. |
| `resume`, `database` | Arquivo de currículo e caminho do SQLite, relativos à pasta do YAML. |

Gupy e GitHub fornecem horários de publicação. LinkedIn e Indeed recebem a janela nativa do JobSpy. InfoJobs e Vagas.com podem informar apenas o dia. O motor usa o início desse dia em São Paulo para decidir se ele cabe na janela. O Nube fornece um sitemap, e a primeira coleta persistente estabelece o inventário. Nas próximas, entram links novos de desenvolvimento quando a última leitura completa ocorreu dentro da janela. APinfo ficou fora da seleção após os testes de extração.

## Conferir o encaixe de uma vaga

O currículo local pode incluir a tabela **Evidências por requisito** do modelo `curriculo.md`. Cada linha liga um fato a uma fonte e a termos da vaga. O motor escolhe até três evidências, favorecendo as que cobrem mais termos. Experiência aplicada, ensino, projeto, formação e certificação aparecem identificados no resultado.

Cada evidência selecionada que acrescenta termos aos já pontuados pode contribuir uma vez. Os pesos são 0,7 para experiência, 0,5 para ensino ou projeto e 0,2 para formação ou certificação, até 1,4 ponto adicional. Os filtros de senioridade, experiência mínima e localização continuam valendo. A nota é uma regra de ordenação, sem interpretação probabilística.

Para avaliar um anúncio já encontrado, salve o título na primeira linha de um TXT e a descrição nas seguintes. Execute `main.py --avaliar-arquivo caminho/para/vaga.txt` com o Python do projeto. Esse comando trabalha localmente, mostra inclusive o motivo de um bloqueio e dispensa data de publicação. O histórico de coletas permanece reservado às consultas aos portais.

O painel mostra as evidências selecionadas no currículo. Quando a análise remota está habilitada, ela recebe um recorte do perfil e as evidências relevantes.

## Aderência por IA

O Gemini configurado faz uma segunda leitura das finalistas. Ele indica dúvidas sobre requisitos, nível e competências. O parecer fica separado da nota local. A vaga continua visível se o modelo der nota zero, errar a senioridade, responder em formato inválido ou esgotar a cota. As cotas do serviço dependem do projeto. [Limites oficiais](https://ai.google.dev/gemini-api/docs/rate-limits)

1. Abra o Google AI Studio e crie uma chave em um projeto que aparece como **Free**, sem faturamento vinculado. [Criar chave](https://aistudio.google.com/apikey) · [Consultar cotas da conta](https://aistudio.google.com/rate-limit)
2. No `.env`, use `AI_PROVIDER=gemini`, `AI_MODEL=gemini-decision-1` e cole a chave depois de `GEMINI_API_KEY=`. A busca funciona por regras enquanto a chave estiver vazia. Para usar sempre as regras, escolha `AI_PROVIDER=none`.
3. Revise `curriculo.local.md` antes da primeira análise remota. Use uma versão com competências e experiências descritas sem identificar pessoas ou clientes. O serviço gratuito do Google pode usar o conteúdo enviado para melhorar os produtos. [Termos do Gemini](https://ai.google.dev/gemini-api/terms)
4. Rode `.\.venv\Scripts\python.exe main.py --hours 24` para coletar e analisar as vagas. Consulte o consumo local a qualquer momento com `.\.venv\Scripts\python.exe main.py --uso-ia`.

O caçador termina a coleta, aplica as regras e elimina duplicatas antes de selecionar finalistas. Primeiro vêm os formulários, com preferência por inscrições verificadas, depois a aderência local. Descrições muito curtas ou sem requisitos e competências aproveitáveis ficam só com as regras. Uma chamada leva até 2.600 caracteres da descrição, 1.050 do perfil e três evidências. Os prompts tratam anúncios como dados externos e pedem que o modelo sinalize incertezas. A análise é reutilizada quando o modelo e o conteúdo da requisição coincidem. A prévia com `--dry-run` usa o cache e dispensa novas chamadas.

O orçamento configurado é de **3 novos pareceres por rodada, 6 chamadas e 24.000 tokens em 24 horas**, com **12.000 tokens por minuto**. As rodadas que usam o mesmo banco compartilham os limites. O programa reserva uma estimativa antes da chamada e atualiza o total quando recebe o consumo do provedor. Ao atingir um limite, a busca pública e a triagem local prosseguem. O comando `--uso-ia` mostra o consumo registrado neste computador.

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

O SQLite reúne anúncios repetidos por URL e cruza publicações compatíveis de outras fontes. O histórico preserva os estados de candidatura. Ao aplicar o novo perfil, as aprovações antigas passam novamente pelas regras locais, inclusive vagas que a IA havia reprovado. Use `main.py --painel` para reconstruir a página a partir do banco.

## Testes e referências

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Os testes usam respostas controladas para validar falhas de fonte, leitura de anúncios, deduplicação, filtros, IA, leitura do painel local e persistência entre rodadas. A consulta real de diagnóstico é feita pela CLI com `--dry-run`.

Os parsers de InfoJobs e Vagas.com e os padrões de links do Nube vêm do **monitor-vagas**, com a licença MIT preservada em `licenses/monitor-vagas.txt`. [Código de origem](https://github.com/joaosousa51/monitor-vagas)

O **JobSpy** (MIT) fornece LinkedIn e Indeed. A rota da Gupy e a leitura da descrição foram verificadas a partir do **radar-de-vagas** (MIT). O **vagas-dev** (GPL-3.0) e o **gupy-jobs-scraper** (MIT) foram consultados na auditoria. As revisões e o resultado por portal estão no [relatório local](AUDITORIA.md). [JobSpy](https://github.com/speedyapply/JobSpy) · [radar-de-vagas](https://github.com/oliveirarenanfelipe/radar-de-vagas) · [vagas-dev](https://github.com/henriquesebastiao/vagas-dev) · [gupy-jobs-scraper](https://github.com/datadmon/gupy-jobs-scraper)
