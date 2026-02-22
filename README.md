# Restaurant BI v3.2 - Prospeccao Inteligente B2B

Sistema de mapeamento de restaurantes com cruzamento **Dados Abertos da Receita Federal** x **Google Maps** para geracao de leads B2B qualificados. Foco: identificar restaurantes que **nao estao no iFood**. Diferencial: **telefone do proprietario** via cnpj.biz.

## Como Funciona

### Fluxo Completo do Pipeline (v3.2)

```
    [A] DADOS ABERTOS RECEITA FEDERAL
    Baixa Estabelecimentos{0-9}.zip (~500MB cada)
    do mirror Casa dos Dados (RF)
    Filtra: CNAE restaurante + ATIVA + UF + cidade
    Streaming CSV dentro do ZIP (sem descompactar)
    3 modos: Capitais | Estado | Cidades especificas
    Controle inteligente: verifica pasta RF mais recente
    antes de re-baixar 4.7GB desnecessariamente
                        |
                        v
            +-------------------------------+
            |     cnpjs_receita (SQLite)     |
            |  CNPJ, nome fantasia, endereco |
            |  telefone, email, CNAE, cidade |
            +-------------------------------+
                        |
                        v
    [B] cnpj.biz (DETALHAMENTO)
    7 tabs simultaneas com Playwright
    Chunks de 200 CNPJs com cool-down entre lotes
    Browser restart automatico em crash/bloqueio
    Deteccao de dados embaralhados (anti-scraping)
    revealAllContacts() para dados mascarados
    3 retries, backoff [5s, 10s, 20s], delay max 8x
    Extrai: TEL PROPRIETARIO + socios + capital social
    Pausa periodica a cada 100 sucessos
    CNPJs com falha: tentativas_falha++ (prioriza retry)
                        |
                        v
            +-------------------------------+
            |    cnpjs_receita atualizada    |
            | + tel_proprietario + socios    |
            | + capital social + natureza    |
            +-------------------------------+
                        |
            +-----------+-----------+
            |                       |
            v (Recomendado)         v (Complementar)
    [F] MAPS DIRECIONADA     [C] MAPS GENERICA
    Para cada CNPJ com        "restaurantes em {cidade}"
    endereco detalhado:       scraping stealth da lista
    busca no Maps pelo        scroll infinito + cards
    endereco/nome fantasia            |
    5 tabs, score >= 0.50             v
    INSERT + vincula            [D] ADDRESS MATCHER
    automatico em transacao     cruza endereco Receita x Maps
            |                   (similaridade >= 0.55)
            |                         |
            +-------------------------+
                        |
                        v
               [E] iFOOD CHECKER
            verifica se esta no iFood
            (nome confirmado: Maps > Receita)
            Pula MEIs sem nome fantasia
                        |
                        v
            +-------------------------------+
            |       LEAD QUALIFICADO        |
            |  CNPJ + socios + endereco     |
            |  + tel_proprietario           |
            |  + presenca iFood (sim/nao)   |
            +-------------------------------+
                        |
                        v
               EXPORTACAO Excel/CSV
            7 abas: Leads Receita,
            Detalhados, Premium,
            Contato, Socios,
            Maps, Resumo
```

### Detalhamento de Cada Etapa

#### [A] Importar Dados Abertos da Receita Federal

| Item | Detalhe |
|------|---------|
| **Fonte** | Mirror Casa dos Dados: `dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/` |
| **Arquivos** | 10 ZIPs `Estabelecimentos{0-9}.zip` (~500MB cada, ~4.7GB total) |
| **Formato CSV** | Separador `;`, encoding `latin-1`, sem header, 28+ colunas |
| **Filtros** | CNAE restaurante + situacao_cadastral `02` (ATIVA) + UF + municipio |
| **CNPJ** | Montado: `cnpj_basico(8) + cnpj_ordem(4) + cnpj_dv(2)` = 14 digitos |
| **Municipios** | Mapeamento codigo RF -> nome via `Municipios.zip` (cacheado em `data/municipios_rf.json`) |
| **Estrategia** | Baixa 1 ZIP, processa via streaming (sem descompactar), deleta, repete |
| **Insert** | `INSERT OR IGNORE` em batch de 5000 (apenas novos) |
| **Dados obtidos** | CNPJ, nome fantasia, endereco completo, telefone, email, CNAE, data abertura |
| **Controle** | Salva data da pasta RF em `controle_atualizacao` - evita re-download |

**3 Modos de importacao:**
1. **Capitais** - 27 capitais brasileiras
2. **Por Estado** - todas as cidades de um estado (sem filtro de municipio)
3. **Cidades especificas** - formato `Cidade/UF, Cidade/UF`

**Controle inteligente de atualizacao (v3.2):**
- Ao iniciar, resolve URL da pasta mais recente no mirror (ex: `2026-01-11`)
- Consulta `controle_atualizacao` para ver ultima pasta importada
- Se datas iguais: avisa "Dados ja atualizados. Importar novamente? [s/N]"
- Se datas diferentes: "Nova atualizacao disponivel"
- Ao concluir: salva pasta, timestamp e modo no banco

#### [B] Detalhar CNPJs via cnpj.biz

| Item | Detalhe |
|------|---------|
| **URL** | `https://cnpj.biz/{cnpj_14_digitos}` |
| **Protecao** | Cloudflare (bypass via Playwright) |
| **Dados mascarados** | `revealAllContacts()` revela email e telefone |
| **Concorrencia** | 7 tabs simultaneas com semaforo |
| **Retry** | 3 tentativas com backoff [5s, 10s, 20s] |
| **Cloudflare** | Detecta "Just a moment" -> pausa 30-60s |
| **Auto-ajuste** | Se 3+ falhas consecutivas: delays * 1.5x (max 8x) |
| **Validacao** | `_dados_sao_validos()`: so marca `detalhado=1` se tem endereco/tel/email/socios |
| **Falhas** | `tentativas_falha++` (NAO marca como detalhado) -> prioriza na proxima varredura |

**Dados extraidos:**
- Telefone do proprietario (celular diferente do da empresa)
- Socios (QSA): nome, qualificacao, tipo PF/PJ
- Capital social, natureza juridica, porte
- Email (do proprietario para MEI/EI)
- Endereco detalhado (logradouro, numero, complemento, bairro, CEP)
- Tipo empresa (MEI, EI, LTDA, SLU, SA, EIRELI)
- Simples Nacional / MEI

**Resiliencia v3.2:**
- **Chunking**: processa em lotes de 200 CNPJs
- **Cool-down**: 60-120s entre cada lote (fecha browser, reabre com novo fingerprint)
- **Browser restart**: detecta "Connection closed", "Target page crashed", "ERR_UNEXPECTED_PROXY_AUTH"
- **Dados embaralhados**: detecta enderecos com consoantes sem sentido (anti-scraping)
- **Pausa periodica**: 30-60s a cada 100 CNPJs processados com sucesso
- **Fingerprint rotativo**: user-agent e viewport aleatorios a cada restart

**Fix v3.2 - banco vazio:**
- Antes: com banco vazio, dizia "todos CNPJs detalhados" (confundia 0 pendentes com tarefa concluida)
- Agora: verifica `COUNT(*)` da cidade primeiro. Se 0 -> "Nenhum CNPJ importado, execute [A] primeiro!"

#### [F] Busca Maps Direcionada (Recomendado)

| Item | Detalhe |
|------|---------|
| **Pre-requisito** | CNPJs com `detalhado=1 AND matched=0 AND logradouro IS NOT NULL` |
| **Concorrencia** | 5 tabs simultaneas (menos que cnpj.biz - Maps detecta bots mais facilmente) |
| **Retry** | 2 tentativas com backoff [5s, 15s] |
| **Score minimo** | 0.50 (mais tolerante que cruzamento generico) |
| **Descarte** | Resultados de outra cidade sao ignorados |

**Queries em ordem de prioridade:**
1. `"{nome_fantasia} {cidade} {uf}"`
2. `"{logradouro} {numero} {cidade} {uf}"`
3. `"{nome_fantasia} {logradouro} {cidade}"`

**Deteccao de resultado:**
- URL com `/place/` = resultado unico (direto para o painel)
- URL com lista = tenta ate 3 primeiros resultados

**Vinculacao:**
- `INSERT restaurante` + `UPDATE cnpjs_receita (matched=1, restaurante_id, score_match)` em transacao unica
- Propaga: telefone proprietario, socios, dados CNPJ para tabela restaurantes

#### [C] Varredura Maps Generica (Complementar)

- Busca `"restaurantes em {cidade}"` no Google Maps
- Scroll infinito na lista lateral (ate 80 scrolls)
- Dois modos: Completo (clica cada card) ou Rapido (extrai da lista)
- Save incremental: cada restaurante salvo imediatamente no DB
- Deduplicacao: chave unica `(nome, cidade, uf)`

#### [D] Cruzamento de Enderecos (Address Matcher)

- Usado **APENAS** com busca generica [C] (nao necessario com [F])
- Normaliza enderecos: remove acentos, expande abreviacoes (R. -> Rua, Av. -> Avenida)
- Score = numero (40%) + logradouro (40%) + bairro (20%)
- Numero diferente = descarta imediatamente (score 0.05)
- Score minimo: 0.55
- Detecta socios com multiplos restaurantes (`multi_restaurante=1`)

#### [E] Verificacao iFood

- Verifica presenca no iFood usando nome confirmado
- Prioridade do nome: Maps (match confirmado) > nome_fantasia (Receita) > pular
- MEIs sem nome fantasia e sem match Maps: pulados automaticamente
- Resultado salvo em `cnpjs_receita` (propagado para restaurantes)

#### [P] Pipeline Completo (v3.2)

**Selecao geografica (novo v3.2):**
```
[1] Capital especifica
[2] Por estado (todas cidades com dados RF)
[3] Cidades especificas (Cidade/UF, Cidade/UF)
[4] Todas as capitais (27 cidades)
```

**Fluxo automatico para cada cidade:**
1. Verifica se dados RF existem, oferece importar se nao
2. **B**: Detalha CNPJs via cnpj.biz (tel proprietario)
3. **F/C+D**: Busca Maps (3 modos: Direcionada, Generica completa, Generica rapida)
4. Detecta socios multi-restaurante
5. **E**: Verifica iFood (nome confirmado)
6. Exporta Excel ao final

### Processamento Incremental

| Etapa | O que evita re-processar |
|-------|--------------------------|
| **[A] RF** | Controle de pasta RF - nao re-baixa 4.7GB se dados ja atualizados |
| **[B] cnpj.biz** | So detalha `detalhado=0`; falhas priorizadas por `tentativas_falha DESC` |
| **[F] Maps Dir** | So busca `detalhado=1 AND matched=0 AND logradouro IS NOT NULL` |
| **[C] Maps Gen** | Chave unica `(nome, cidade, uf)` - nao re-insere existentes |
| **[D] Cruzamento** | So cruza restaurantes sem CNPJ vinculado |
| **[E] iFood** | So verifica `tem_ifood=0 AND ifood_nome IS NULL` |

## Modulos

| Arquivo | Funcao |
|---------|--------|
| `main.py` | Orquestrador - menu v3.2, pipeline com selecao geografica |
| `config.py` | Constantes (delays, URLs, capitais, CNAEs, UFs) |
| `init_db.py` | Criacao de tabelas SQLite, indices, migracoes e `controle_atualizacao` |
| `db_manager.py` | CRUD restaurantes, socios, varreduras, vinculacao CNPJ-Maps |
| `receita_federal.py` | Download e importacao Dados Abertos RF (10 ZIPs, streaming CSV) |
| `receita_fetcher.py` | Detalhamento CNPJs via cnpj.biz (tel prop, socios) + resiliencia v3.2 |
| `gmaps_scraper.py` | Scraping Google Maps via Playwright (generico + direcionado) |
| `address_matcher.py` | Motor de cruzamento enderecos Receita x Maps (similaridade) |
| `ifood_checker.py` | Verificacao presenca no iFood via Playwright |
| `exporter.py` | Exportacao Excel/CSV (pandas + openpyxl) - 7 abas |
| `logger.py` | Logging dual: terminal + `Logs_secoes/logs_YYYY-MM-DD.txt` |
| `simulacao.py` | Demo visual com dados ficticios de Curitiba/PR |

## Fontes de Dados

| Fonte | Endpoint | Dados | Protecao |
|-------|----------|-------|----------|
| Dados Abertos RF | `dados-abertos-rf-cnpj.casadosdados.com.br` | CNPJ, endereco, telefone, email, CNAE | Nenhuma (download HTTP) |
| cnpj.biz | `GET cnpj.biz/{cnpj}` | Tel proprietario, socios, capital social | Cloudflare (Playwright) |
| Google Maps | Scraping Playwright | Nome, endereco, tel, rating, reviews | Anti-bot Google |
| iFood | Scraping Playwright | Presenca na plataforma | Anti-bot iFood |

## Banco de Dados (SQLite)

### Tabelas

| Tabela | Descricao | Chave unica |
|--------|-----------|-------------|
| `restaurantes` | Dados Maps + dados enriquecidos (CNPJ, socios, tel prop) | `(nome, cidade, uf)` |
| `socios` | QSA dos restaurantes. FK para `restaurantes.id` (CASCADE) | - |
| `cnpjs_receita` | Base da Receita Federal por CNAE + detalhamento cnpj.biz | `cnpj` |
| `varreduras` | Controle de varreduras Maps | `(cidade, uf)` |
| `varreduras_receita` | Controle de varreduras Receita | `(cidade, uf, cnae)` |
| `controle_atualizacao` | Controle de versao dos dados (pasta RF, timestamps) | `chave` (PK) |

### Campos importantes de cnpjs_receita

| Campo | Tipo | Descricao |
|-------|------|-----------|
| `detalhado` | 0/1 | Se foi detalhado via cnpj.biz |
| `matched` | 0/1 | Se foi vinculado a restaurante do Maps |
| `restaurante_id` | INTEGER | FK para restaurantes (quando matched=1) |
| `score_match` | REAL | Score de similaridade do endereco |
| `telefone_proprietario` | TEXT | Telefone pessoal do dono (cnpj.biz) |
| `tentativas_falha` | INTEGER | Quantas vezes detalhamento falhou (prioriza retry) |
| `ultima_falha` | TEXT | Timestamp da ultima falha |
| `tem_ifood` | 0/1 | Se esta no iFood |
| `multi_restaurante` | 0/1 | Socio com 2+ CNPJs |
| `fonte` | TEXT | `dados_abertos` (RF) |
| `fonte_detalhamento` | TEXT | `cnpjbiz` |

### Fluxo de Status (restaurantes)

```
pendente -> processado (Maps ok) -> ifood_checked -> enriquecido (CNPJ vinculado)
```

### Pragmas SQLite

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
```

## Configuracoes (config.py)

### cnpj.biz
| Parametro | Valor | Descricao |
|-----------|-------|-----------|
| `CNPJBIZ_CONCURRENT_TABS` | 7 | Tabs simultaneas |
| `CNPJBIZ_DELAY_MIN/MAX` | 5-12s | Delay entre requisicoes |
| `CNPJBIZ_MAX_RETRIES` | 3 | Tentativas por CNPJ |
| `CNPJBIZ_RETRY_BACKOFF` | [5, 10, 20]s | Backoff entre retries |
| `CNPJBIZ_CLOUDFLARE_PAUSE` | 30-60s | Pausa ao detectar Cloudflare |

### Resiliencia v3.2 (receita_fetcher.py)
| Parametro | Valor | Descricao |
|-----------|-------|-----------|
| `CHUNK_SIZE` | 200 | CNPJs por lote |
| `CHUNK_COOLDOWN` | 60-120s | Pausa entre lotes |
| `PERIODIC_PAUSE_EVERY` | 100 | Pausa a cada N sucessos |
| `CRASH_PAUSE` | 90-120s | Pausa apos crash/bloqueio |
| `MAX_DELAY_MULTIPLIER` | 8.0x | Limite maximo do auto-ajuste |
| `CONSECUTIVE_FAIL_THRESHOLD` | 5 | Falhas antes de pausa longa |

### Google Maps Direcionado
| Parametro | Valor | Descricao |
|-----------|-------|-----------|
| `GMAPS_DIRECTED_CONCURRENT_TABS` | 5 | Tabs simultaneas |
| `GMAPS_DIRECTED_DELAY_MIN/MAX` | 5-12s | Delay entre buscas |
| `GMAPS_DIRECTED_SCORE_MINIMO` | 0.50 | Score minimo para match |
| `GMAPS_DIRECTED_MAX_RETRIES` | 2 | Tentativas por CNPJ |

### CNAEs de Restaurante
```
5611201 - Restaurantes e similares
5611202 - Bares e outros com servico de alimentacao
5611203 - Lanchonetes, casas de cha, de sucos e similares
5612100 - Servicos ambulantes de alimentacao
```

## Diretorios

```
data/                    # Banco SQLite (restaurants.db) + municipios_rf.json
data/receita_federal/    # ZIPs temporarios da RF (baixados e deletados)
exports/                 # Arquivos Excel/CSV exportados
Logs_secoes/             # Logs diarios (logs_YYYY-MM-DD.txt)
.venv/                   # Ambiente virtual Python
```

## Como Rodar

```bash
# Instalar dependencias
pip install -r requirements.txt
playwright install chromium

# Ativar ambiente virtual
source .venv/bin/activate

# Rodar o sistema
python main.py

# Inicializar banco manualmente (opcional)
python init_db.py

# Rodar simulacao/demo
python simulacao.py

# Testar algoritmo de match
python address_matcher.py
```

### Menu Principal v3.2

```
-- PIPELINE --
[A] Importar Dados Abertos Receita Federal
[B] Detalhar CNPJs (cnpj.biz + Tel Proprietario)
[C] Varredura Maps (generica - por cidade)
[F] Busca Maps Direcionada (por endereco CNPJ)
[D] Cruzar Enderecos (cnpj.biz x Maps)
[E] Verificar iFood (nome confirmado)

-- AUTOMATICO --
[P] Pipeline Completo (B+F+E por cidade/estado)

-- CONSULTAS --
[5] Consultar Banco    [6] Exportar Excel
[7] Exportar CSV       [8] Estatisticas
[T] Testar Algoritmo   [9] Resetar
[0] Sair
```

## Stack Tecnica

- **Linguagem**: Python 3.12
- **Ambiente virtual**: `.venv/`
- **Banco de dados**: SQLite (`data/restaurants.db`)
- **Scraping**: Playwright (Chromium, modo async)
- **HTTP**: httpx (async)
- **Dados**: pandas, openpyxl
- **UI terminal**: rich
- **Sem framework web** - aplicacao CLI com menu interativo

## Changelog

### v3.2 (2026-02-22)
- Fix: "todos CNPJs detalhados" com banco vazio
- Controle inteligente de atualizacao RF (evita re-download de 4.7GB)
- Resiliencia cnpj.biz: chunking 200, browser restart, deteccao dados embaralhados
- Pipeline [P] com selecao geografica (capital/estado/cidades/todas)
- Resumo RF melhorado: modo, novos vs existentes, pasta RF

### v3.1 (2026-02-17)
- Dados Abertos RF substituindo Casa dos Dados API
- Busca Maps Direcionada [F]
- Pipeline completo A+B+F+E
- cnpj.biz como fonte unica de detalhamento

### v3.0 (2026-02-16)
- cnpj.biz com telefone do proprietario
- Address matcher para cruzamento enderecos
- Exportacao Excel com 7 abas
- iFood checker com nome confirmado
