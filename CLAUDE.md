# CLAUDE.md - Restaurant BI v3.1

## Idioma

Sempre responda em **portugues brasileiro (pt-BR)** em todas as interacoes.

## Visao Geral do Projeto

Sistema de prospeccao B2B para restaurantes. Mapeia restaurantes de capitais brasileiras cruzando dados da Receita Federal com Google Maps para gerar leads qualificados. Foco em identificar restaurantes que **nao estao no iFood** como oportunidade comercial. Diferencial: **telefone do proprietario** via cnpj.biz.

## Stack Tecnica

- **Linguagem**: Python 3.12
- **Ambiente virtual**: `.venv/` (ativar com `source .venv/bin/activate`)
- **Banco de dados**: SQLite (`data/restaurants.db`)
- **Scraping**: Playwright (Chromium, modo async)
- **HTTP**: httpx (async)
- **Dados**: pandas, openpyxl
- **UI terminal**: rich
- **Sem framework web** - aplicacao CLI com menu interativo

## Comandos Essenciais

```bash
# Ativar ambiente
cd ~/Hacking-restaurant-b2b && source .venv/bin/activate

# Rodar o sistema
python main.py

# Instalar dependencias
pip install -r requirements.txt
playwright install chromium

# Inicializar banco manualmente
python init_db.py

# Rodar simulacao (demo com dados ficticios)
python simulacao.py

# Testar algoritmo de match de enderecos
python address_matcher.py
```

## Arquitetura e Modulos

```
main.py                 # Orquestrador - menu interativo, pipeline completo
config.py               # Constantes globais (delays, URLs, capitais, status)
init_db.py              # Criacao de tabelas SQLite, indices e migracao
db_manager.py           # CRUD e consultas SQLite (restaurantes, socios, varreduras)
receita_fetcher.py      # Coleta CNPJs via Casa dos Dados API v5 + cnpj.biz (tel proprietario, UNICA fonte)
gmaps_scraper.py        # Scraping Google Maps via Playwright (generico + direcionado)
address_matcher.py      # Motor de cruzamento enderecos Receita x Maps (similaridade)
ifood_checker.py        # Verificacao presenca no iFood via Playwright
exporter.py             # Exportacao Excel/CSV formatado (pandas + openpyxl) + aba Leads Premium
logger.py               # Logging dual: terminal + Logs_secoes/logs_YYYY-MM-DD.txt
simulacao.py            # Demo visual com dados ficticios de Curitiba/PR
```

## Pipeline de Dados

```
[A] Casa dos Dados API v5 (CNAE+cidade+ATIVA) --> cnpjs_receita (dados basicos)
    - API protegida por Cloudflare -> Playwright resolve challenge
    - Usa page.evaluate() para chamar API v5 de dentro do browser
    - Abre UMA sessao de browser para todos os CNAEs (eficiente)
    - Retorna: CNPJ, razao social, nome fantasia (sem endereco)
    - Paginacao dinamica: calcula total de paginas baseado na resposta da API

[B] cnpj.biz (detalha cada CNPJ) --> atualiza cnpjs_receita
    - FONTE PRIMARIA: telefone do proprietario (O OURO DO SISTEMA)
    - Preenche: endereco completo, socios, email, telefone, capital social
    - 7 tabs simultaneas, 3 retries com backoff (5s,10s,20s)
    - Timeout: pausa aleatoria 1.5x-3x do backoff + auto-ajuste global
    - CNPJs com falha: registra tentativas_falha++ para priorizar na proxima varredura
    - SEM fallback: cnpj.biz e a UNICA fonte (OpenCNPJ removido)
    - _dados_sao_validos(): so marca detalhado=1 se tem endereco/tel/email/socios

[F] Busca Maps Direcionada (RECOMENDADO para pipeline)
    - Para cada CNPJ detalhado com endereco, busca no Maps
    - Queries: nome_fantasia+cidade, endereco+cidade, combinado
    - 5 tabs simultaneas, 2 retries com backoff [5s, 15s]
    - Detecta resultado unico (/place/) vs lista (ate 3 primeiros)
    - Score minimo: 0.50 | Descarta resultados de outra cidade
    - INSERT restaurante + vincula CNPJ em uma transacao
    - Nenhum lead perdido: CNPJs sem match continuam na base

[C] Google Maps scraping generico --> tabela restaurantes
    - Save incremental: cada restaurante salvo imediatamente no DB
    - Deduplicacao: pula restaurantes ja extraidos (por nome e URL)
    - Retomada: ao reiniciar, continua de onde parou

[D] Address Matcher --> cruza enderecos cnpj.biz x Maps, vincula CNPJ
    - Usado APENAS com busca generica [C] (nao necessario com [F])
    - Propaga telefone_proprietario, dados iFood para restaurantes
    - Detecta socios com multiplos restaurantes (multi_restaurante=1)

[E] iFood check (APOS cruzamento) --> atualiza cnpjs_receita.tem_ifood
    - Usa nome confirmado: Maps > nome_fantasia > pula MEIs sem nome
    - Resultado salvo em cnpjs_receita (propagado no cruzamento)

[P] Pipeline Completo = A + B + [F ou C+D] + E (automatico) + Exportacao
    - 3 modos: Direcionado (recomendado), Generico completo, Generico rapido
```

## Banco de Dados (SQLite)

### Tabelas principais
- **restaurantes**: dados do Google Maps + dados enriquecidos (CNPJ, socios, tel proprietario). Chave unica: `(nome, cidade, uf)`
- **socios**: QSA dos restaurantes. FK para `restaurantes.id`
- **cnpjs_receita**: base da Receita Federal por CNAE. Chave unica: `cnpj`
  - Campos de controle: `detalhado`, `matched`, `restaurante_id`, `score_match`, `tentativas_falha`, `ultima_falha`
  - Campos de contato: `telefone_proprietario`, `email`, `email_proprietario`
  - Campos iFood: `tem_ifood`, `ifood_nome`, `ifood_url`
  - Campos empresa: `tipo_empresa`, `tipo_negocio`, `fonte_detalhamento`, `multi_restaurante`
- **varreduras**: controle de varreduras do Google Maps. Chave unica: `(cidade, uf)`
- **varreduras_receita**: controle de varreduras da Receita. Chave unica: `(cidade, uf, cnae)`

### Status do restaurante (fluxo)
`pendente` -> `processado` (Maps ok) -> `ifood_checked` -> `enriquecido` (CNPJ vinculado)

### Pragmas usados
- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`

## Funcoes Chave por Modulo

### db_manager.py
- `inserir_restaurante()`, `inserir_restaurantes_batch()`: CRUD restaurantes
- `atualizar_cnpj()`: propaga dados CNPJ para restaurante
- `buscar_cnpjs_para_maps_direcionado(cidade, uf)`: CNPJs com detalhado=1, matched=0, tem logradouro
- `inserir_restaurante_e_vincular(dados_maps, dados_cnpj)`: INSERT restaurante + UPDATE cnpjs_receita (matched=1) em transacao
- `buscar_leads_receita()`: TODOS os CNPJs (para export)
- `buscar_leads_detalhados()`: so com dados completos

### gmaps_scraper.py
- `scrape_restaurantes_cidade()`: varredura generica por cidade
- `scrape_cidade_simples()`: varredura rapida (sem clicar em cards)
- `scrape_maps_direcionado(cidade, uf, headless, callback)`: busca direcionada por endereco CNPJ
- `_construir_queries_maps(cnpj_data)`: gera queries em ordem de prioridade
- `_extrair_detalhes_lugar(page)`: extrai dados do painel lateral do Maps
- `_buscar_cnpj_no_maps(page, cnpj_data)`: tenta queries, calcula score
- `_buscar_um_cnpj_maps(page, cnpj_data, semaphore, stats)`: wrapper com retry

### receita_fetcher.py
- `coletar_cnpjs_cidade()`: passo A (Casa dos Dados)
- `detalhar_cnpjs_cidade()`: passo B (cnpj.biz + OpenCNPJ)
- `obter_cnpjs_nao_detalhados()`: ORDER BY tentativas_falha DESC (prioriza falhas)
- `registrar_falha_cnpj()`: incrementa tentativas_falha + timestamp

## Padroes do Codigo

- Todas as funcoes de scraping sao **async** (asyncio + Playwright async API)
- Conexoes SQLite sao abertas/fechadas por operacao (sem pool)
- `db_manager.py` centraliza todo acesso ao banco (exceto `receita_fetcher.py` que tem acesso direto para cnpjs_receita)
- Anti-deteccao: user-agents rotativos, delays randomicos, scripts anti-webdriver
- Processamento **incremental**: nunca re-processa dados ja coletados
- CNAEs de restaurante: 5611201, 5611202, 5611203, 5612100
- Nomes de cidades sao normalizados (acentos removidos) antes de enviar para APIs
- Detalhamento usa cnpj.biz como fonte primaria, OpenCNPJ como fallback
- Todos os modulos usam `from logger import log` em vez de print
- Logs: `logger.py` -> terminal + `Logs_secoes/logs_YYYY-MM-DD.txt` (append por dia)

## Diretorios

```
data/            # Banco SQLite (restaurants.db)
exports/         # Arquivos Excel/CSV exportados
Logs_secoes/     # Logs diarios (logs_YYYY-MM-DD.txt)
.venv/           # Ambiente virtual Python
__pycache__/     # Cache Python
```

## APIs Externas

- **Casa dos Dados v5**: `POST https://api.casadosdados.com.br/v5/public/cnpj/pesquisa`
  - Busca CNPJs por CNAE+cidade+situacao
  - Protegida por Cloudflare (requer Playwright para resolver challenge)
  - Payload: `codigo_atividade_principal`, `uf`, `municipio`, `situacao_cadastral`, `pagina`
  - Resposta: `{"total": N, "cnpjs": [{"cnpj", "razao_social", "nome_fantasia", "situacao_cadastral"}]}`
  - 20 resultados por pagina, max 500 paginas por CNAE (10.000 resultados)
  - Municipios sem acento e em UPPERCASE (ex: "SAO PAULO", nao "SÃO PAULO")
- **cnpj.biz**: `GET https://cnpj.biz/{cnpj}` (scraping via Playwright)
  - FONTE PRIMARIA de detalhamento (tel proprietario!)
  - Protegida por Cloudflare (requer Playwright)
  - Dados: endereco, socios, email, telefone proprietario, capital social, tipo empresa
  - Config: 7 tabs, delay 5-12s, 3 retries, backoff [5,10,20]s
- **OpenCNPJ**: `GET https://api.opencnpj.org/{cnpj}` (DESATIVADO - codigo mantido mas nao usado)
- **Google Maps**: scraping via Playwright (nao e API oficial)
  - Busca direcionada: 5 tabs, delay 5-12s, 2 retries, score minimo 0.50
  - Busca generica: delay 8-20s, scroll infinito
- **iFood**: scraping via Playwright + tentativa de API marketplace

## Convencoes

- Logs usam prefixos: `[LOG]`, `[DB]`, `[RECEITA]`, `[DETALHE]`, `[MATCH]`, `[MAPS-DIR]`, `[iFood]`, `[EXPORT]`, `[ERRO]`, `[WARN]`
- Cidades armazenadas em UPPERCASE na tabela cnpjs_receita
- Score minimo de match: 0.55 (generico) / 0.50 (direcionado)
- Delays configurados em config.py (GMAPS_DIRECTED_*, CNPJBIZ_*, MIN_DELAY/MAX_DELAY)

## Cuidados Importantes

- **Nao ha .gitignore**: o banco SQLite e o .venv estao fora do git mas devem continuar assim
- `receita_fetcher.py` duplica a criacao de tabelas que ja existe em `init_db.py` (via `init_tabela_receita()`)
- `simulacao.py` e apenas para demonstracao, nao afeta o banco real (usa `simulacao.db`)
- O scraping do Google Maps pode ser bloqueado - o sistema usa tecnicas stealth mas nao ha garantia
- Rate limiting da OpenCNPJ: ~50 req/s permitido, mas o codigo usa delays conservadores
- Casa dos Dados API v5 retorna apenas dados basicos (sem endereco) - o endereco vem do cnpj.biz na etapa B
- cnpj.biz pode bloquear por excesso de requisicoes - delays configurados em config.py
- `init_db.py` inclui funcao `_migrar_banco()` que adiciona colunas novas em bancos existentes via ALTER TABLE
- Busca direcionada usa 5 tabs (menos que cnpj.biz) porque Google detecta bots mais facilmente
- CNPJs com falha de timeout no cnpj.biz sao registrados (tentativas_falha) e priorizados na proxima varredura
