# CLAUDE.md - Restaurant BI v4.1

## Idioma

Sempre responda em **portugues brasileiro (pt-BR)** em todas as interacoes.

## Visao Geral do Projeto

Sistema de prospeccao B2B para restaurantes. Mapeia restaurantes de capitais brasileiras cruzando dados da Receita Federal com Google Maps para gerar leads qualificados. Foco em identificar restaurantes que **nao estao em plataformas de delivery** (iFood, Rappi, 99Food) como oportunidade comercial.

**v4.0**: RF Expandido (Estabelecimentos + Empresas + Simples + Socios) torna cnpj.biz **OPCIONAL**. Apenas o telefone pessoal do proprietario e exclusivo do cnpj.biz.

**v4.1**: `browser_manager.py` centraliza criacao de browser + anti-deteccao. Pause breaks aleatorios (2-35min ponderados) em todos os scrapers. Circuit breaker reativo. Sessoes de 500 CNPJs no Maps direcionado. Timeout reduzido de 45s para 20s.

## Stack Tecnica

- **Linguagem**: Python 3.12
- **Ambiente virtual**: `.venv/` (ativar com `source .venv/bin/activate`)
- **Banco de dados**: SQLite (`data/restaurants.db`)
- **Scraping**: Playwright (Chromium, modo async) + playwright-stealth
- **HTTP**: httpx[socks] (async, com suporte SOCKS5)
- **Proxy**: Tor (SOCKS5) ou proxy customizado (VPN/residencial) — fallback para conexao direta
- **Dados**: pandas, openpyxl
- **UI terminal**: rich
- **Controle Tor**: stem (ControlPort para NEWNYM)
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

# Configurar Tor (opcional - requer sudo)
sudo bash setup_tor.sh

# Inicializar banco manualmente
python init_db.py

# Rodar simulacao (demo com dados ficticios)
python simulacao.py

# Testar algoritmo de match de enderecos
python address_matcher.py
```

## Arquitetura e Modulos

```
main.py                 # Orquestrador - menu interativo, pipeline completo v4.0
config.py               # Constantes globais (delays, URLs, capitais, status, proxy/Tor, delivery, pause breaks)
init_db.py              # Criacao de tabelas SQLite, indices e migracao (v4.0: delivery+RF expandido)
db_manager.py           # CRUD e consultas SQLite (restaurantes, socios, varreduras)
browser_manager.py      # Gerenciamento central de browser: criar/fechar, pause breaks, CircuitBreaker, stealth
receita_federal.py      # Download e importacao RF (Estabelecimentos+Empresas+Simples+Socios)
receita_fetcher.py      # Detalhamento CNPJs via cnpj.biz (OPCIONAL) + funcoes DB + delivery
gmaps_scraper.py        # Scraping Google Maps via Playwright (generico + direcionado)
address_matcher.py      # Motor de cruzamento enderecos Receita x Maps (similaridade)
delivery_checker.py     # Verificacao multi-plataforma (iFood+Rappi+99Food) com micro-batches
ifood_checker.py        # Verificacao presenca no iFood via Playwright (legado, mantido)
exporter.py             # Exportacao Excel/CSV formatado + aba Sem Delivery
logger.py               # Logging dual: terminal + Logs_secoes/logs_YYYY-MM-DD.txt
simulacao.py            # Demo visual com dados ficticios de Curitiba/PR
setup_tor.sh            # Script de instalacao e configuracao do Tor (sudo)
cnpj_enricher.py        # Enriquecimento CNPJ com proxy Tor/customizado
```

## Pipeline de Dados (v4.0)

```
[A] Dados Abertos Receita Federal --> cnpjs_receita (COMPLETO)
    - Estabelecimentos{0-9}.zip: CNPJ, nome, endereco, telefone, email, CNAE
    - Empresas.zip: razao_social, capital_social, porte, natureza_juridica
    - Simples.zip: opcao_simples (S/N), opcao_mei (S/N)
    - Socios.zip: QSA (nome, qualificacao, tipo PF/PJ, CPF mascarado)
    - Filtragem: Estabelecimentos por CNAE+UF, complementares por cnpj_basico
    - Marca detalhado=1 automaticamente quando tem endereco + dados uteis
    - 3 modos: Capitais (27), Por Estado, Por Cidades especificas

[B] cnpj.biz (OPCIONAL - so para tel proprietario pessoal)
    - Removido do pipeline automatico [P]
    - Disponivel no menu manual [B] para quem precisa do tel pessoal do dono
    - 1 tab, delay 12-25s, limite 500/sessao, anti-Cloudflare

[F] Busca Maps Direcionada
    - v4.0: nao exige detalhado=1 (RF de Estabelecimentos ja tem endereco)
    - Para cada CNPJ com logradouro, busca no Maps
    - 5 tabs independentes (sem Semaphore), 2 retries, score minimo 0.50
    - v4.1: sessoes de 500 CNPJs, pause breaks por tab, circuit breaker
    - Timeout reduzido para 20s, resetar_tab apos timeout

[C] Google Maps scraping generico --> tabela restaurantes

[D] Address Matcher --> cruza enderecos Receita x Maps

[E] Delivery Multi-plataforma (iFood + Rappi + 99Food)
    - Micro-batches de 15-25 restaurantes por sessao de browser
    - Browser restart entre batches (novo fingerprint)
    - Teste de acesso pre-batch (pula plataforma inacessivel)
    - Delay 3-6s entre queries, 8-15s a cada 5

[P] Pipeline v4.0 = A (se sem dados) + [F ou Hibrido ou C+D] + E + Exportacao
    - SEM cnpj.biz no pipeline (RF ja tem dados completos)
    - 4 modos Maps: Direcionada, Hibrido (RECOMENDADO), Generica completa, Generica rapida
    - Modo Hibrido: Direcionada -> Generica -> Cruzamento (cobertura maxima)
```

## Banco de Dados (SQLite)

### Tabelas principais
- **restaurantes**: dados do Google Maps + dados enriquecidos. Chave unica: `(nome, cidade, uf)`
  - Campos delivery: `tem_ifood`, `tem_rappi`, `tem_99food` + nomes + URLs
- **socios**: QSA dos restaurantes. FK para `restaurantes.id`
- **cnpjs_receita**: base da Receita Federal por CNAE. Chave unica: `cnpj`
  - Campos de controle: `detalhado`, `matched`, `enriquecido_rf`, `restaurante_id`, `score_match`
  - Campos de contato: `telefone_proprietario`, `email`, `email_proprietario`
  - Campos delivery: `tem_ifood`, `tem_rappi`, `tem_99food` + nomes + URLs
  - Campos empresa: `tipo_empresa`, `tipo_negocio`, `capital_social`, `porte`, `natureza_juridica`
  - Campos RF: `socios_json`, `simples`, `mei`, `data_opcao_simples`
- **varreduras**: controle de varreduras do Google Maps
- **varreduras_receita**: controle de varreduras da Receita
- **controle_atualizacao**: controle de versao dos dados RF

### Campos de controle enriquecido_rf (v4.0)
- `enriquecido_rf = 0`: CNPJ so tem dados de Estabelecimentos
- `enriquecido_rf = 1`: CNPJ complementado por Empresas+Simples+Socios da RF
- `detalhado = 1`: setado automaticamente quando tem endereco + dados uteis (sem precisar cnpj.biz)

### Status do restaurante (fluxo)
`pendente` -> `processado` (Maps ok) -> `ifood_checked` -> `enriquecido` (CNPJ vinculado)

### Pragmas usados
- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`

## Funcoes Chave por Modulo

### browser_manager.py (v4.1 - novo)
- `criar_browser(pw, headless)`: browser com fingerprint rotativo (user-agent + viewport + stealth)
- `fechar_browser(browser)`: encerramento seguro com try/except
- `gerar_pausa_break()`: duracao ponderada (55% 2-5min, 30% 8-15min, 15% 20-35min)
- `gerar_limite_pause_break()`: itens antes do proximo break (30-50, via config)
- `executar_pause_break(browser)`: fecha browser + espera + loga
- `CircuitBreaker`: pausa reativa (3 timeouts -> pausa curta 1-3min, 5 erros -> pausa longa 5-10min)
- `resetar_tab(page)`: navega para about:blank apos timeout
- `VIEWPORTS`: lista de viewports (fonte unica, removido de delivery_checker.py)

### receita_federal.py (v4.0 - principal mudanca)
- `importar_receita_federal()`: orquestrador (Estabelecimentos + complementares)
- `baixar_arquivo_complementar(nome)`: baixa Empresas/Simples/Socios.zip
- `_carregar_cnpjs_basicos_existentes()`: retorna set de cnpj_basico (8 digitos)
- `_processar_empresas_zip()`: atualiza razao_social, capital, porte, natureza
- `_processar_simples_zip()`: atualiza simples, mei, data_opcao_simples
- `_processar_socios_zip()`: agrupa socios por CNPJ, salva como JSON
- `_marcar_enriquecido_rf()`: detalhado=1 para CNPJs com endereco + dados uteis

### delivery_checker.py (v4.0 - novo)
- `verificar_delivery_cidade(cidade, uf, headless, plataformas)`: verifica todas as plataformas
- `verificar_plataforma_batch(items, plataforma, headless)`: verifica uma plataforma com micro-batches
- `_testar_acesso_plataforma(pw, plataforma)`: teste pre-batch
- `_processar_micro_batch(pw, items, plataforma, batch_num)`: browser novo por batch

### db_manager.py
- `buscar_cnpjs_para_maps_direcionado(cidade, uf)`: v4.0: nao exige detalhado=1
- `inserir_restaurante_e_vincular(dados_maps, dados_cnpj)`: INSERT + UPDATE em transacao

### receita_fetcher.py
- `atualizar_delivery_receita(cnpj, plataforma, tem, nome, url)`: generico por plataforma
- `obter_cnpjs_sem_delivery(cidade, uf, plataforma)`: generaliza obter_cnpjs_sem_ifood
- `detalhar_cnpjs_cidade()`: passo B (cnpj.biz - OPCIONAL)

## Anti-Ban e Proxy

### Cadeia de Fallback de Proxy (cnpj.biz)
```
1. PROXY_CUSTOM (se definido) — VPN SOCKS5, proxy residencial, etc.
2. Tor SOCKS5 (se TOR_ENABLED=True e Tor rodando) — exit nodes rotativos
3. Conexao direta (fallback final) — IP real do usuario
```

### Browser Manager — Pause Breaks (v4.1)
| Parametro | Valor | Objetivo |
|-----------|-------|----------|
| `BROWSER_SESSION_LIMIT` | 500 | CNPJs por sessao de browser (Maps direcionado) |
| `PAUSE_BREAK_MIN/MAX_ITEMS` | 30-50 | Itens entre pause breaks |
| `GMAPS_DIRECTED_TIMEOUT` | 20000 (20s) | Timeout reduzido (era 45s) |

**Distribuicao de pause breaks (ponderada):**
- 55% curta: 2-5 min
- 30% media: 8-15 min
- 15% longa: 20-35 min

**Aplicacao por scraper:**
- `gmaps_scraper.py` (direcionado): pause break por tab (max 5min) + entre sessoes de 500
- `gmaps_scraper.py` (generico): pause break a cada 30-50 cards (max 5min, sem fechar browser)
- `delivery_checker.py`: pause break longo a cada 30-50 itens totais + cooldown normal entre micro-batches
- `ifood_checker.py`: micro-batches de 30-50 com browser restart + pause break entre sessoes

**CircuitBreaker (reativo, independente dos pause breaks):**
- 3 timeouts consecutivos -> pausa curta (1-3 min)
- 5 erros consecutivos -> pausa longa (5-10 min)
- Sucesso reseta contadores

### Delivery Multi-plataforma (v4.0)
| Parametro | Valor | Objetivo |
|-----------|-------|----------|
| `DELIVERY_MICRO_BATCH_MIN/MAX` | 15-25 | Tamanho aleatorio por sessao de browser |
| `DELIVERY_BATCH_COOLDOWN_MIN/MAX` | 30-60s | Pausa entre micro-batches (browser restart) |
| `DELIVERY_DELAY_MIN/MAX` | 3-6s | Delay entre queries normais |
| `DELIVERY_LONG_PAUSE_EVERY` | 5 | Pausa longa a cada N queries |
| `DELIVERY_DELAY_LONG_MIN/MAX` | 8-15s | Delay da pausa longa |

### Deteccao de Bloqueio (3 niveis - cnpj.biz)
1. **Ban**: abort imediato
2. **Challenge**: espera 8-15s e tenta novamente
3. **Normal**: extrai dados

## Padroes do Codigo

- Todas as funcoes de scraping sao **async** (asyncio + Playwright async API)
- Conexoes SQLite sao abertas/fechadas por operacao (sem pool)
- Anti-deteccao: user-agents rotativos, viewports rotativos, delays randomicos, playwright-stealth, pause breaks ponderados
- Criacao de browser centralizada em `browser_manager.py` (usado por gmaps, delivery, ifood)
- Processamento **incremental**: nunca re-processa dados ja coletados
- CNAEs de restaurante: 5611201, 5611202, 5611203, 5612100
- Todos os modulos usam `from logger import log` em vez de print
- Imports opcionais com try/except: `stem`, `playwright_stealth`, `httpx`
- cnpj.biz **OPCIONAL** no pipeline (RF tem socios, capital, porte, simples/MEI)
- `_detectar_cloudflare()` retorna string (`"ban"`, `"challenge"`, `""`) — NAO booleano

## Diretorios

```
data/                    # Banco SQLite (restaurants.db) + municipios_rf.json
data/receita_federal/    # ZIPs temporarios da RF (baixados e deletados)
exports/                 # Arquivos Excel/CSV exportados
Logs_secoes/             # Logs diarios (logs_YYYY-MM-DD.txt)
.venv/                   # Ambiente virtual Python
__pycache__/             # Cache Python
```

## Fontes de Dados

- **Dados Abertos Receita Federal**: `https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/`
  - 10 arquivos Estabelecimentos (~500MB cada): CNPJ, endereco, telefone, email
  - 1 arquivo Empresas.zip (~100MB): razao_social, capital, porte, natureza juridica
  - 1 arquivo Simples.zip (~30MB): opcao simples/MEI
  - 1 arquivo Socios.zip (~100MB): QSA completo
  - Municipios.zip: mapeamento codigo RF -> nome cidade
  - CSV com separador `;`, encoding latin-1, sem header
  - Atualizacao mensal pela Receita Federal
- **cnpj.biz** (OPCIONAL): `GET https://cnpj.biz/{cnpj}` (scraping via Playwright)
  - UNICO dado exclusivo: telefone pessoal do proprietario
  - Protegida por Cloudflare, config conservadora
- **Google Maps**: scraping via Playwright (nao e API oficial)
  - Busca direcionada: 5 tabs, score minimo 0.50
  - Busca generica: delay 8-20s, scroll infinito
- **Delivery Multi-plataforma** (v4.0):
  - iFood: `ifood.com.br/busca`
  - Rappi: `rappi.com.br/restaurantes/busca`
  - 99Food: `99food.com.br/busca` (pode ser didi-food.com)

## Convencoes

- Logs usam prefixos: `[LOG]`, `[DB]`, `[RF]`, `[DETALHE]`, `[MATCH]`, `[MAPS-DIR]`, `[iFood]`, `[DELIVERY]`, `[EXPORT]`, `[ERRO]`, `[WARN]`, `[TOR]`, `[PAUSE]`, `[CB]`, `[RESET]`
- Cidades armazenadas em UPPERCASE na tabela cnpjs_receita
- Score minimo de match: 0.55 (generico) / 0.50 (direcionado)
- Delays configurados em config.py

## Cuidados Importantes

- **Nao ha .gitignore**: o banco SQLite e o .venv estao fora do git
- `receita_fetcher.py` duplica a criacao de tabelas que ja existe em `init_db.py` (via `init_tabela_receita()`)
- `simulacao.py` e apenas para demonstracao, nao afeta o banco real (usa `simulacao.db`)
- `init_db.py` inclui funcao `_migrar_banco()` que adiciona colunas novas via ALTER TABLE
- **NUNCA usar `wait_until="networkidle"` no cnpj.biz** — causa timeout de 45s
- Cloudflare bloqueia exit nodes do Tor — prefira proxy residencial ou reset de router/IP
- `TOR_ENABLED = False` por padrao
- playwright-stealth v2.0.2 usa `Stealth().apply_stealth_async(context)`, NAO `stealth_async(context)`
- cnpj.biz e **OPCIONAL** no v4.0 — RF ja fornece socios, capital, porte, simples/MEI
- Arquivos complementares (Empresas/Simples/Socios) contem TODOS os CNPJs do Brasil — filtrar por set de cnpj_basico existentes
- delivery_checker.py usa micro-batches com browser restart para evitar fingerprinting
- 99Food pode estar inacessivel — teste de acesso pre-batch pula automaticamente
- `VIEWPORTS` definido APENAS em `browser_manager.py` — fonte unica (nao duplicar)
- `scrape_maps_direcionado` usa sessoes de 500 CNPJs com pause breaks (NAO Semaphore)
- `_buscar_um_cnpj_maps` NAO usa semaphore — cada tab opera independente
- Apos timeout no Maps, sempre chamar `resetar_tab(page)` antes do proximo goto
- `ifood_checker.py` usa micro-batches de 30-50 com browser restart (v4.1)

## Derekh CRM — Central de Captacao

**Nome**: Derekh Food
**Stack**: FastAPI + Jinja2 + Tailwind CSS (CDN) + Chart.js (CDN) + PostgreSQL (fly.io)
**Banco CRM**: PostgreSQL no fly.io (database `derekh_crm`) — scraping continua SQLite local
**Rodar local**: `DATABASE_URL=postgres://localhost/derekh_crm uvicorn crm.app:app --reload --port 8000`
**Deploy**: `fly deploy --app derekh-crm`
**Sync**: `python sync_crm.py` (SQLite local -> PostgreSQL fly.io)

### Arquivos CRM
```
crm/schema.sql          # Schema PostgreSQL (leads, interacoes, email_templates, campanhas, sequencias)
crm/database.py         # Pool psycopg2, 40+ queries (dashboard, busca, ficha, pipeline, email, campanhas, sequencias)
crm/models.py           # Constantes (pipeline, segmentos, interacoes, campanhas, WhatsApp templates)
crm/scoring.py          # Lead scoring 0-100, segmentacao automatica, personalizacao abordagem
crm/email_service.py    # Integracao Resend API, envio, campanhas batch, webhook, preview
crm/whatsapp_service.py # Links wa.me personalizados com templates
crm/app.py              # FastAPI: 30+ rotas (dashboard, busca, ficha, pipeline, email, WhatsApp, webhook)
crm/templates/          # 13 templates HTML (base, dashboard, busca, ficha, pipeline, email*, whatsapp)
sync_crm.py             # Sync SQLite -> PostgreSQL (upsert por CNPJ, protege campos CRM)
Dockerfile              # Deploy CRM no fly.io
fly.toml                # Config fly.io (app derekh-crm, regiao gru)
requirements-crm.txt    # FastAPI, psycopg2-binary, resend, uvicorn, jinja2
.env.example             # Variaveis de ambiente
```

### Checkpoints Implementacao
- (x) **Fase 1**: Schema PostgreSQL + reescrita database.py
- (x) **Fase 2**: Templates HTML (base + dashboard + busca + ficha + pipeline)
- (x) **Fase 3**: Lead scoring + segmentacao (`crm/scoring.py`)
- (x) **Fase 4**: Email marketing (Resend + templates + campanhas + sequencias + webhook)
- (x) **Fase 5**: WhatsApp (links wa.me + templates personalizados)
- (x) **Fase 6**: Deploy (Dockerfile + fly.toml + sync_crm.py)
- ( ) **Fase 7**: Agente de auditoria (revisao de codigo + performance + seguranca)
