# Restaurant BI v4.1 - Prospeccao Inteligente B2B

Sistema de mapeamento de restaurantes com cruzamento **Dados Abertos da Receita Federal** x **Google Maps** para geracao de leads B2B qualificados. Foco: identificar restaurantes que **nao estao em plataformas de delivery** (iFood, Rappi, 99Food) como oportunidade comercial.

**v4.0**: RF Expandido (Estabelecimentos + Empresas + Simples + Socios) torna cnpj.biz **OPCIONAL**. Delivery multi-plataforma.

**v4.1**: `browser_manager.py` centraliza anti-deteccao. Pause breaks aleatorios ponderados em todos os scrapers. Circuit breaker reativo. Sessoes de 500 CNPJs no Maps direcionado.

## Como Funciona

### Fluxo Completo do Pipeline (v4.1)

```
    [A] DADOS ABERTOS RECEITA FEDERAL
    Estabelecimentos{0-9}.zip (~500MB cada) + Empresas + Simples + Socios
    Filtra: CNAE restaurante + ATIVA + UF + cidade
    Streaming CSV dentro do ZIP (sem descompactar)
    3 modos: Capitais | Estado | Cidades especificas
    v4.0: RF ja fornece socios, capital, porte, simples/MEI
                        |
                        v
            +-------------------------------+
            |     cnpjs_receita (SQLite)     |
            |  CNPJ, endereco, telefone,    |
            |  socios, capital, porte,      |
            |  simples/MEI, natureza        |
            +-------------------------------+
                        |
    [B] cnpj.biz (OPCIONAL - so tel proprietario pessoal)
                        |
            +-----------+-----------+
            |                       |
            v (Recomendado)         v (Complementar)
    [F] MAPS DIRECIONADA     [C] MAPS GENERICA
    Para cada CNPJ com        "restaurantes em {cidade}"
    logradouro, busca Maps    scraping stealth + scroll
    5 tabs independentes              |
    Sessoes de 500 CNPJs              v
    Pause breaks + CB          [D] ADDRESS MATCHER
    Score >= 0.50              cruza endereco RF x Maps
            |                   (similaridade >= 0.55)
            +-------------------------+
                        |
                        v
          [E] DELIVERY MULTI-PLATAFORMA
            iFood + Rappi + 99Food
            Micro-batches com browser restart
            Pause breaks ponderados (2-35min)
                        |
                        v
            +-------------------------------+
            |       LEAD QUALIFICADO        |
            |  CNPJ + socios + endereco     |
            |  + presenca delivery (3 plat) |
            +-------------------------------+
                        |
                        v
               EXPORTACAO Excel/CSV
            + aba "Sem Delivery"
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
| **Protecao** | Cloudflare (bypass via Playwright + playwright-stealth) |
| **Dados mascarados** | `revealAllContacts()` revela email e telefone |
| **Concorrencia** | 1 tab (conservador para preservar IP) |
| **Delay** | 12-25s entre requisicoes (simula navegacao humana) |
| **Limite diario** | 500 CNPJs por sessao (`CNPJBIZ_DAILY_LIMIT`) |
| **Retry** | 3 tentativas com backoff [5s, 10s, 20s] |
| **Cloudflare ban** | Detecta "You have been blocked" → **abort imediato** de todo o batch |
| **Cloudflare challenge** | Detecta "Just a moment" → **espera 8-15s**, tenta novamente |
| **CNPJ inexistente** | Detecta "Alguma coisa saiu errado" → marca no DB, nao gasta retry |
| **Auto-ajuste** | Se 3+ falhas consecutivas: delays * 1.5x (max 8x) |
| **Validacao** | `_dados_sao_validos()`: so marca `detalhado=1` se tem endereco/tel/email/socios |
| **Falhas** | `tentativas_falha++` (NAO marca como detalhado) → prioriza na proxima varredura |
| **Page load** | `wait_until="domcontentloaded"` + `wait_for_selector('body')` |

**Dados extraidos:**
- Telefone do proprietario (celular diferente do da empresa)
- Socios (QSA): nome, qualificacao, tipo PF/PJ
- Capital social, natureza juridica, porte
- Email (do proprietario para MEI/EI)
- Endereco detalhado (logradouro, numero, complemento, bairro, CEP)
- Tipo empresa (MEI, EI, LTDA, SLU, SA, EIRELI)
- Simples Nacional / MEI

**Logs detalhados por CNPJ (v3.2):**
```
[DETALHE] 12345678000199: TEL PROP=11999887766 | TEL=1133445566 | EMAIL=dono@email.com | END=Rua X 123 | SOCIOS=2
[DETALHE] 98765432000188: INEXISTENTE no cnpj.biz
[DETALHE] 11223344000155: sem dados uteis (somente dados basicos)
```

**Resiliencia v3.2:**
- **Teste pre-batch**: `_testar_acesso_cnpjbiz()` verifica se o site esta acessivel antes de iniciar loop de CNPJs. Se inacessivel, aborta imediatamente com mensagem clara
- **Chunking**: processa em lotes de 200 CNPJs
- **Cool-down**: 60-120s entre cada lote (fecha browser, reabre com novo fingerprint)
- **Browser restart**: detecta "Connection closed", "Target page crashed", "ERR_PROXY_CONNECTION_FAILED", "ERR_SOCKS_CONNECTION_FAILED"
- **Dados embaralhados**: detecta enderecos com consoantes sem sentido (anti-scraping)
- **Pausa periodica**: 30-60s a cada 100 CNPJs processados com sucesso
- **Fingerprint rotativo**: user-agent e viewport aleatorios a cada restart
- **Early-abort**: se site inacessivel (ban/proxy error), nao percorre todos os CNPJs

**Fix v3.2 - banco vazio:**
- Antes: com banco vazio, dizia "todos CNPJs detalhados" (confundia 0 pendentes com tarefa concluida)
- Agora: verifica `COUNT(*)` da cidade primeiro. Se 0 → "Nenhum CNPJ importado, execute [A] primeiro!"

#### Anti-Ban e Sistema de Proxy (v3.2)

**Cadeia de fallback de proxy:**
```
PROXY_CUSTOM (se definido)     → VPN SOCKS5, proxy residencial, etc.
        |
        v (se vazio)
Tor SOCKS5 (se TOR_ENABLED)   → Exit nodes rotativos (NEWNYM a cada chunk)
        |
        v (se desativado/offline)
Conexao direta                 → IP real do usuario (fallback final)
```

**Configuracao em config.py:**
```python
# Proxy customizado (prioridade sobre Tor)
PROXY_CUSTOM = ""  # Ex: "socks5://127.0.0.1:1080" (NordVPN, etc.)

# Tor (fallback se PROXY_CUSTOM vazio)
TOR_ENABLED = False            # Cloudflare bloqueia exit nodes do Tor
TOR_SOCKS_PORT = 9050          # Proxy SOCKS5
TOR_CONTROL_PORT = 9051        # Sinal NEWNYM para novo circuito
TOR_CONTROL_PASSWORD = "..."   # Senha do ControlPort
TOR_NEWNYM_COOLDOWN = 12       # Segundos entre rotacoes de IP
```

**IMPORTANTE**: O Cloudflare do cnpj.biz **bloqueia a maioria dos exit nodes do Tor** (retorna 403 "you have been blocked"). Para contornar bans de IP:
1. **Reset de router** (ISP movel): desligue, espere 1 minuto, religue → novo IP
2. **Proxy residencial** (IPRoyal, BrightData): IPs residenciais nao bloqueados
3. **VPN com IPs rotativos**: menos eficaz que residencial, mas funciona em muitos casos

**Funcoes Tor em receita_fetcher.py:**
| Funcao | Descricao |
|--------|-----------|
| `_tor_socks_disponivel()` | Testa conexao socket ao SocksPort 9050 |
| `_tor_control_disponivel()` | Testa autenticacao stem no ControlPort 9051 |
| `_tor_disponivel()` | True se TOR_ENABLED e SOCKS acessivel |
| `_tor_trocar_ip(motivo)` | Envia NEWNYM via stem, respeita cooldown 12s |
| `_tor_verificar_ip()` | Consulta IP atual via httpx+SOCKS5 proxy |
| `_obter_proxy_config()` | Retorna dict proxy (PROXY_CUSTOM > Tor > vazio) |
| `_detectar_cloudflare(page)` | Retorna `"ban"` / `"challenge"` / `""` |
| `_detectar_bloqueio_cnpjbiz(page)` | Detecta Network Check, Proxy Auth |
| `_testar_acesso_cnpjbiz(pw)` | Teste pre-batch com retry+NEWNYM |

**Setup do Tor (opcional):**
```bash
sudo bash setup_tor.sh
# Instala tor, configura ControlPort 9051 com senha hasheada,
# SocksPort 9050, verifica ambas as portas, testa IP de saida

# Teste manual:
curl --socks5-hostname 127.0.0.1:9050 https://api.ipify.org?format=json
```

#### CNPJ Inexistente no cnpj.biz (v3.2)

Nem todo CNPJ da Receita Federal existe no banco do cnpj.biz. Quando o CNPJ nao existe, o site retorna uma pagina com:
- Titulo: "Consulta de CNPJ: Dados de Empresas do Brasil"
- Texto: "Opa! Alguma coisa saiu errado. Desculpe, mas esta empresa nao esta mais no nosso banco de dados."

**Antes (v3.1):** Tratava como falha de extracao → gastava 3 retries → registrava como `tentativas_falha++`
**Agora (v3.2):**
1. Detecta antes da extracao de dados (verifica titulo + texto da pagina)
2. Marca `cnpjbiz_inexistente = 1` no banco (`marcar_cnpj_inexistente_cnpjbiz()`)
3. Log claro: `[DETALHE] CNPJ INEXISTENTE no cnpj.biz`
4. NAO gasta retries (retorna imediatamente)
5. NAO incrementa `tentativas_falha` (nao e erro, e dado ausente)
6. Na fila: `ORDER BY cnpjbiz_inexistente ASC, tentativas_falha ASC` → inexistentes vao pro FINAL

**Coluna no banco:**
```sql
ALTER TABLE cnpjs_receita ADD COLUMN cnpjbiz_inexistente INTEGER DEFAULT 0;
```

#### wait_until="domcontentloaded" (Fix critico v3.2)

**Problema:** cnpj.biz tem scripts de analytics/trackers que **nunca param de carregar**. O Playwright com `wait_until="networkidle"` esperava ate o timeout (45s) em TODA pagina, tornando o detalhamento impraticavelmente lento.

**Solucao:**
```python
# ANTES (v3.1) - 45s por pagina
await page.goto(url, wait_until="networkidle", timeout=45000)

# DEPOIS (v3.2) - 2-5s por pagina
await page.goto(url, wait_until="domcontentloaded", timeout=45000)
await page.wait_for_selector('body', timeout=10000)
```

**Impacto:** 4 ocorrencias alteradas em `receita_fetcher.py`. Paginas que demoravam 45s agora carregam em 2-5s. Todos os dados (TEL PROP, EMAIL, END, SOCIOS) ja estao disponiveis no `domcontentloaded`.

#### [F] Busca Maps Direcionada (Recomendado)

| Item | Detalhe |
|------|---------|
| **Pre-requisito** | CNPJs com `matched=0 AND logradouro IS NOT NULL` (v4.0: nao exige detalhado=1) |
| **Concorrencia** | 5 tabs independentes (sem Semaphore — cada tab opera na sua page) |
| **Sessoes** | 500 CNPJs por sessao de browser (`BROWSER_SESSION_LIMIT`), com pause breaks entre sessoes |
| **Pause breaks** | A cada 30-50 CNPJs por tab (max 5min) + entre sessoes (2-35min ponderado) |
| **Circuit breaker** | 3 timeouts → pausa curta 1-3min, 5 erros → pausa longa 5-10min |
| **Timeout** | 20s (reduzido de 45s) + `resetar_tab()` apos timeout |
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

#### [E] Delivery Multi-plataforma (v4.0+)

- Verifica presenca em iFood + Rappi + 99Food
- Micro-batches de 15-25 restaurantes com browser restart entre batches
- v4.1: pause breaks ponderados (2-35min) a cada 30-50 itens totais
- Teste de acesso pre-batch (pula plataforma inacessivel)
- Prioridade do nome: Maps (match confirmado) > nome_fantasia (Receita) > pular
- ifood_checker.py (legado): refatorado com micro-batches 30-50 + pause breaks

#### [P] Pipeline Completo (v4.0)

**Selecao geografica:**
```
[1] Capital especifica
[2] Por estado (todas cidades com dados RF)
[3] Cidades especificas (Cidade/UF, Cidade/UF)
[4] Todas as capitais (27 cidades)
```

**Fluxo automatico para cada cidade:**
1. Verifica se dados RF existem, oferece importar se nao
2. **F/C+D**: Busca Maps (4 modos: Direcionada, Hibrido, Generica completa, Generica rapida)
3. Detecta socios multi-restaurante
4. **E**: Verifica delivery multi-plataforma (iFood + Rappi + 99Food)
5. Exporta Excel ao final (inclui aba "Sem Delivery")

**Nota v4.0:** cnpj.biz removido do pipeline automatico (RF ja tem dados completos). Disponivel no menu manual [B].

### Processamento Incremental

| Etapa | O que evita re-processar |
|-------|--------------------------|
| **[A] RF** | Controle de pasta RF - nao re-baixa 4.7GB se dados ja atualizados |
| **[B] cnpj.biz** | So detalha `detalhado=0 AND cnpjbiz_inexistente=0`; falhas priorizadas por `tentativas_falha ASC` |
| **[F] Maps Dir** | So busca `detalhado=1 AND matched=0 AND logradouro IS NOT NULL` |
| **[C] Maps Gen** | Chave unica `(nome, cidade, uf)` - nao re-insere existentes |
| **[D] Cruzamento** | So cruza restaurantes sem CNPJ vinculado |
| **[E] iFood** | So verifica `tem_ifood=0 AND ifood_nome IS NULL` |

## Modulos

| Arquivo | Funcao |
|---------|--------|
| `main.py` | Orquestrador - menu v4.0, pipeline com selecao geografica |
| `config.py` | Constantes (delays, URLs, capitais, CNAEs, proxy/Tor, delivery, pause breaks) |
| `browser_manager.py` | **v4.1** - Gerenciamento central de browser: criar/fechar, pause breaks, CircuitBreaker, stealth |
| `init_db.py` | Criacao de tabelas SQLite, indices, migracoes e `controle_atualizacao` |
| `db_manager.py` | CRUD restaurantes, socios, varreduras, vinculacao CNPJ-Maps |
| `receita_federal.py` | Download e importacao RF expandida (Estabelecimentos+Empresas+Simples+Socios) |
| `receita_fetcher.py` | Detalhamento CNPJs via cnpj.biz (OPCIONAL) + funcoes DB + delivery |
| `gmaps_scraper.py` | Scraping Google Maps (generico + direcionado com sessoes de 500 + pause breaks) |
| `address_matcher.py` | Motor de cruzamento enderecos Receita x Maps (similaridade) |
| `delivery_checker.py` | **v4.0** - Verificacao multi-plataforma (iFood+Rappi+99Food) com micro-batches |
| `ifood_checker.py` | Verificacao presenca no iFood (legado, refatorado com micro-batches v4.1) |
| `exporter.py` | Exportacao Excel/CSV (pandas + openpyxl) + aba "Sem Delivery" |
| `logger.py` | Logging dual: terminal + `Logs_secoes/logs_YYYY-MM-DD.txt` |
| `simulacao.py` | Demo visual com dados ficticios de Curitiba/PR |
| `cnpj_enricher.py` | Enriquecimento CNPJ com suporte a proxy Tor/customizado |
| `setup_tor.sh` | Script de instalacao e configuracao do Tor (sudo) |

## Fontes de Dados

| Fonte | Endpoint | Dados | Protecao |
|-------|----------|-------|----------|
| Dados Abertos RF | `dados-abertos-rf-cnpj.casadosdados.com.br` | CNPJ, endereco, telefone, socios, capital, porte, simples/MEI | Nenhuma (download HTTP) |
| cnpj.biz (OPCIONAL) | `GET cnpj.biz/{cnpj}` | Tel pessoal do proprietario (unico exclusivo) | Cloudflare (Playwright + stealth) |
| Google Maps | Scraping Playwright | Nome, endereco, tel, rating, reviews | Anti-bot Google |
| iFood | Scraping Playwright | Presenca na plataforma | Anti-bot iFood |
| Rappi | Scraping Playwright | Presenca na plataforma | Anti-bot |
| 99Food | Scraping Playwright | Presenca na plataforma | Anti-bot (pode estar inacessivel) |

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
| `cnpjbiz_inexistente` | 0/1 | CNPJ nao existe no banco do cnpj.biz (v3.2) |
| `tem_ifood` | 0/1 | Se esta no iFood |
| `tem_rappi` | 0/1 | Se esta no Rappi |
| `tem_99food` | 0/1 | Se esta no 99Food |
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

### cnpj.biz (conservador para preservar IP)
| Parametro | Valor | Descricao |
|-----------|-------|-----------|
| `CNPJBIZ_CONCURRENT_TABS` | 1 | 1 tab (menos agressivo) |
| `CNPJBIZ_DELAY_MIN/MAX` | 12-25s | Delay longo entre requisicoes |
| `CNPJBIZ_MAX_RETRIES` | 3 | Tentativas por CNPJ |
| `CNPJBIZ_RETRY_BACKOFF` | [5, 10, 20]s | Backoff entre retries |
| `CNPJBIZ_CLOUDFLARE_PAUSE` | 30-60s | Pausa ao detectar Cloudflare challenge |
| `CNPJBIZ_DAILY_LIMIT` | 500 | Maximo de CNPJs por sessao (0 = sem limite) |
| `CNPJBIZ_REVEAL_WAIT` | 4s | Espera apos revealAllContacts() |
| `CNPJBIZ_TIMEOUT` | 45000ms | Timeout de navegacao |

### Proxy e Tor
| Parametro | Valor | Descricao |
|-----------|-------|-----------|
| `PROXY_CUSTOM` | `""` | Proxy custom (SOCKS5/HTTP) — prioridade sobre Tor |
| `TOR_ENABLED` | `False` | Ativar Tor (Cloudflare bloqueia exit nodes) |
| `TOR_SOCKS_PORT` | 9050 | Porta SOCKS5 do Tor |
| `TOR_CONTROL_PORT` | 9051 | Porta ControlPort do Tor (NEWNYM) |
| `TOR_NEWNYM_COOLDOWN` | 12s | Cooldown entre rotacoes de IP |
| `TOR_VERIFY_IP_URL` | `api.ipify.org` | URL para verificar IP de saida |

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
| `GMAPS_DIRECTED_CONCURRENT_TABS` | 5 | Tabs independentes (sem Semaphore) |
| `GMAPS_DIRECTED_DELAY_MIN/MAX` | 5-12s | Delay entre buscas |
| `GMAPS_DIRECTED_SCORE_MINIMO` | 0.50 | Score minimo para match |
| `GMAPS_DIRECTED_MAX_RETRIES` | 2 | Tentativas por CNPJ |
| `GMAPS_DIRECTED_TIMEOUT` | 20000ms (20s) | Timeout reduzido (era 45s) |

### Browser Manager — Pause Breaks Anti-Deteccao (v4.1)
| Parametro | Valor | Descricao |
|-----------|-------|-----------|
| `BROWSER_SESSION_LIMIT` | 500 | CNPJs por sessao de browser (Maps direcionado) |
| `PAUSE_BREAK_MIN/MAX_ITEMS` | 30-50 | Itens entre pause breaks |
| Pausa curta (55%) | 2-5 min | Distribuicao ponderada |
| Pausa media (30%) | 8-15 min | Distribuicao ponderada |
| Pausa longa (15%) | 20-35 min | Distribuicao ponderada |
| CircuitBreaker timeouts | 3 | Timeouts consecutivos → pausa curta 1-3min |
| CircuitBreaker erros | 5 | Erros consecutivos → pausa longa 5-10min |

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

# (Opcional) Configurar Tor
sudo bash setup_tor.sh

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

### Menu Principal v4.0

```
-- PIPELINE --
[A] Importar Dados Abertos Receita Federal
[B] Detalhar CNPJs (cnpj.biz - OPCIONAL, tel proprietario)
[C] Varredura Maps (generica - por cidade)
[F] Busca Maps Direcionada (por endereco CNPJ)
[D] Cruzar Enderecos (RF x Maps)
[E] Verificar Delivery (iFood + Rappi + 99Food)

-- AUTOMATICO --
[P] Pipeline Completo (A+F+E por cidade/estado)

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
- **Scraping**: Playwright (Chromium, modo async) + playwright-stealth
- **HTTP**: httpx[socks] (async, suporte SOCKS5 para Tor)
- **Proxy**: Tor SOCKS5 + stem (ControlPort) — ou proxy customizado
- **Dados**: pandas, openpyxl
- **UI terminal**: rich
- **Sem framework web** - aplicacao CLI com menu interativo

## Changelog

### v4.1 (2026-02-25)
- **browser_manager.py**: modulo central de gerenciamento de browser anti-deteccao
  - `criar_browser()`: fingerprint rotativo (user-agent + viewport + stealth)
  - `VIEWPORTS`: fonte unica (removido duplicata de delivery_checker.py)
  - Pause breaks ponderados: 55% curta (2-5min), 30% media (8-15min), 15% longa (20-35min)
  - `CircuitBreaker`: 3 timeouts → pausa 1-3min, 5 erros → pausa 5-10min
  - `resetar_tab()`: about:blank apos timeout para evitar navegacao conflitante
- **gmaps_scraper.py**:
  - `scrape_maps_direcionado`: removido `Semaphore(1)` (tabs independentes), sessoes de 500 CNPJs, pause breaks por tab + entre sessoes
  - `_buscar_um_cnpj_maps`: sem semaphore, com circuit breaker e resetar_tab
  - `scrape_restaurantes_cidade`: pause breaks a cada 30-50 cards
  - `GMAPS_DIRECTED_TIMEOUT`: 45s → 20s
- **delivery_checker.py**: usa `criar_browser()`, pause breaks longos a cada 30-50 itens
- **ifood_checker.py**: refatorado com micro-batches 30-50 + browser restart + pause breaks
- **config.py**: `BROWSER_SESSION_LIMIT=500`, `PAUSE_BREAK_MIN/MAX_ITEMS=30-50`

### v4.0 (2026-02-25)
- **RF Expandido**: processa 4 tipos de arquivo (Estabelecimentos+Empresas+Simples+Socios)
- **cnpj.biz OPCIONAL**: removido do pipeline automatico [P] (RF ja tem socios, capital, porte, simples/MEI)
- **Delivery multi-plataforma**: iFood + Rappi + 99Food via delivery_checker.py
- Micro-batches (15-25) com browser restart, teste pre-batch por plataforma
- Novas colunas: tem_rappi, rappi_nome, rappi_url, tem_99food, food99_nome, food99_url
- `buscar_cnpjs_para_maps_direcionado()` nao exige mais detalhado=1
- Maps hibrido: Direcionada -> Generica -> Cruzamento (4 modos)
- Exporter: novas colunas delivery + aba "Sem Delivery"

### v3.2 (2026-02-24)
- **Anti-ban cnpj.biz**: config conservadora (1 tab, 12-25s delay, 500/sessao)
- **Proxy chain**: PROXY_CUSTOM > Tor SOCKS5 > conexao direta (fallback)
- **Fix critico**: `wait_until="domcontentloaded"` (era `"networkidle"` - timeout 45s)
- **CNPJ inexistente**: detecta, marca no DB (`cnpjbiz_inexistente=1`), vai pro final da fila
- **Cloudflare 3 niveis**: ban (abort) vs challenge (espera) vs normal
- **Teste pre-batch**: `_testar_acesso_cnpjbiz()` aborta cedo se site inacessivel
- **Logs detalhados**: mostra TEL PROP, TEL, EMAIL, END, SOCIOS por CNPJ
- **Tor integrado**: stem + SOCKS5 + NEWNYM (mas Cloudflare bloqueia exit nodes)
- **playwright-stealth**: `Stealth().apply_stealth_async(context)` para anti-deteccao
- **setup_tor.sh**: script de instalacao e configuracao do Tor
- Controle inteligente de atualizacao RF (evita re-download de 4.7GB)
- Resiliencia cnpj.biz: chunking 200, browser restart, deteccao dados embaralhados
- Pipeline [P] com selecao geografica (capital/estado/cidades/todas)
- Fix: "todos CNPJs detalhados" com banco vazio

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
