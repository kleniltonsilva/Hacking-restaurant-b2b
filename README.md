# Restaurant BI v3.1 - Prospeccao Inteligente B2B

Sistema de mapeamento de restaurantes com cruzamento Receita Federal x Google Maps para geracao de leads B2B qualificados. Foco: identificar restaurantes que **nao estao no iFood**. Diferencial: **telefone do proprietario** via cnpj.biz.

## Como Funciona

### Fluxo do Pipeline Completo (v3.1)

```
                     [A] CASA DOS DADOS (API v5)
                  busca por CNAE + cidade + ATIVA
                  (bypass Cloudflare via Playwright)
                              |
                              v
                  +---------------------------+
                  |    cnpjs_receita (SQLite)  |
                  |  CNPJ, razao social, nome  |
                  +---------------------------+
                              |
                              v
                  [B] cnpj.biz (7 tabs simultaneas)
                 TEL PROPRIETARIO + endereco + socios
                 (Cloudflare bypass, 3 retries, backoff)
                 (UNICA fonte - sem fallback)
                              |
                              v
                  +---------------------------+
                  |   cnpjs_receita atualizada |
                  | + tel_prop + end + socios  |
                  +---------------------------+
                              |
              +---------------+----------------+
              |                                |
              v (Modo Direcionado)             v (Modo Generico)
     [F] BUSCA MAPS DIRECIONADA        [C] VARREDURA MAPS GENERICA
     Para cada CNPJ com endereco:       "restaurantes em {cidade}"
     busca no Maps por endereco         scraping stealth da lista
     Score >= 0.50 = match              |
     INSERT + vincula automatico        v
              |                    [D] ADDRESS MATCHER
              |                    cruza endereco Receita x Maps
              |                    (similaridade >= 0.55)
              |                         |
              +-------------------------+
                              |
                              v
                     [E] iFOOD CHECKER
                  verifica se esta no iFood
                  (nome confirmado: Maps > Receita)
                              |
                              v
                  +---------------------------+
                  | LEAD QUALIFICADO          |
                  | CNPJ + socios + endereco  |
                  | + tel_proprietario        |
                  | + presenca iFood          |
                  +---------------------------+
                              |
                              v
                     EXPORTACAO Excel/CSV
              (7 abas: Receita, Detalhados,
               Premium, Contato, Socios,
               Maps, Resumo)
```

### Etapas do Menu

| Etapa | Descricao | Fonte |
|-------|-----------|-------|
| **[A]** | Coletar CNPJs da Receita Federal por CNAE+cidade | Casa dos Dados API v5 |
| **[B]** | Detalhar CNPJs (cnpj.biz + tel proprietario) | cnpj.biz (unica fonte) |
| **[C]** | Varredura Google Maps generica (por cidade) | Google Maps (Playwright) |
| **[F]** | Busca Maps Direcionada (por endereco do CNPJ) | Google Maps (Playwright) |
| **[D]** | Cruzar enderecos Receita x Maps | Algoritmo de similaridade |
| **[E]** | Verificar presenca no iFood | iFood (Playwright) |
| **[P]** | Pipeline Completo Automatico (A+B+F/C+D+E) | Todos acima |

### Dois Modos de Busca no Maps

**Modo Direcionado [F]** (recomendado):
- Para cada CNPJ com endereco (etapa B), busca especificamente no Maps
- Gera queries: nome_fantasia + cidade, endereco + cidade, combinado
- Score de match >= 0.50 = confirma vinculacao
- 5 tabs simultaneas, retry com backoff
- Taxa de match muito superior ao generico

**Modo Generico [C]** (complementar):
- Busca "restaurantes em {cidade}" no Maps
- Retorna subconjunto popular da cidade
- Precisa do passo D (cruzamento) para vincular CNPJs
- Util como complemento para descobrir restaurantes nao cadastrados na Receita

### Processamento Incremental

- **Receita**: so baixa CNPJs novos (nao re-baixa existentes)
- **cnpj.biz**: so detalha pendentes; CNPJs com falha (`tentativas_falha`) sao priorizados na proxima varredura
- **Maps Direcionado**: so busca CNPJs com `detalhado=1 AND matched=0 AND logradouro IS NOT NULL`
- **Maps Generico**: so insere restaurantes novos (chave unica: nome+cidade+uf)
- **Cruzamento**: so cruza restaurantes sem CNPJ vinculado
- **iFood**: so verifica CNPJs sem resultado anterior

## Modulos

| Arquivo | Funcao |
|---------|--------|
| `main.py` | Orquestrador - menu interativo, pipeline completo |
| `config.py` | Constantes globais (delays, URLs, capitais, status) |
| `init_db.py` | Criacao de tabelas SQLite, indices e migracao |
| `db_manager.py` | CRUD e consultas SQLite (restaurantes, socios, varreduras) |
| `receita_fetcher.py` | Coleta CNPJs via Casa dos Dados API v5 + cnpj.biz (tel proprietario, unica fonte) |
| `gmaps_scraper.py` | Scraping stealth do Google Maps via Playwright (generico + direcionado) |
| `address_matcher.py` | Motor de cruzamento enderecos Receita x Maps (similaridade) |
| `ifood_checker.py` | Verificacao presenca no iFood via Playwright |
| `exporter.py` | Exportacao Excel/CSV formatado (pandas + openpyxl) + aba Leads Premium |
| `logger.py` | Logging dual: terminal + arquivo `Logs_secoes/logs_YYYY-MM-DD.txt` |
| `simulacao.py` | Demo visual com dados ficticios de Curitiba/PR |

## APIs e Fontes Externas

| Fonte | Endpoint | Funcao | Protecao |
|-------|----------|--------|----------|
| Casa dos Dados v5 | `POST api.casadosdados.com.br/v5/public/cnpj/pesquisa` | Lista CNPJs por CNAE+cidade | Cloudflare (Playwright bypass) |
| cnpj.biz | `GET cnpj.biz/{cnpj}` | Tel proprietario, endereco, socios, email | Cloudflare (Playwright bypass) |
| OpenCNPJ | `GET api.opencnpj.org/{cnpj}` | DESATIVADO (codigo mantido) | Nenhuma |
| Google Maps | Scraping via Playwright | Restaurantes: nome, endereco, tel, rating | Anti-bot Google |
| iFood | Scraping via Playwright | Presenca na plataforma | Anti-bot iFood |

## Banco de Dados (SQLite)

### Tabelas

| Tabela | Descricao | Chave unica |
|--------|-----------|-------------|
| `restaurantes` | Dados do Maps + dados enriquecidos (CNPJ, socios, tel prop) | `(nome, cidade, uf)` |
| `socios` | QSA dos restaurantes. FK para `restaurantes.id` | - |
| `cnpjs_receita` | Base da Receita Federal por CNAE | `cnpj` |
| `varreduras` | Controle de varreduras Maps | `(cidade, uf)` |
| `varreduras_receita` | Controle de varreduras Receita | `(cidade, uf, cnae)` |

### Campos importantes de cnpjs_receita

- `detalhado` (0/1): se foi detalhado via cnpj.biz/OpenCNPJ
- `matched` (0/1): se foi vinculado a um restaurante do Maps
- `restaurante_id`: FK para restaurantes (quando matched=1)
- `score_match`: score de similaridade do endereco
- `telefone_proprietario`: telefone pessoal do dono (cnpj.biz)
- `tentativas_falha`: quantas vezes o detalhamento falhou (para priorizar retry)
- `tem_ifood` (0/1): se esta no iFood
- `multi_restaurante` (0/1): socio com 2+ CNPJs

### Fluxo de Status (restaurantes)

`pendente` -> `processado` (Maps ok) -> `ifood_checked` -> `enriquecido` (CNPJ vinculado)

## Detalhes Tecnicos

### Casa dos Dados API v5
- Payload: `codigo_atividade_principal`, `uf`, `municipio` (SEM ACENTO, UPPERCASE), `situacao_cadastral: ["ATIVA"]`
- 20 resultados por pagina, paginacao via campo `pagina`
- Resposta: `{"total": N, "cnpjs": [{"cnpj", "razao_social", "nome_fantasia"}]}`
- Retorna apenas dados basicos - sem endereco
- CNAEs: 5611201, 5611202, 5611203, 5612100

### cnpj.biz (Detalhamento)
- 7 tabs simultaneas com Playwright
- 3 retries com backoff exponencial (5s, 10s, 20s)
- `revealAllContacts()` para revelar dados mascarados
- Deteccao de Cloudflare com pausa automatica (30-60s)
- Auto-ajuste: se >3 falhas consecutivas, delays * 1.5x
- Timeout handling: pausa aleatoria 1.5x-3x do backoff
- CNPJs com falha registrados (`tentativas_falha++`) para priorizar na proxima varredura
- SEM fallback: cnpj.biz e a UNICA fonte (OpenCNPJ desativado)
- Validacao: `_dados_sao_validos()` - so marca detalhado=1 se tem endereco/tel/email/socios

### Busca Maps Direcionada (novo v3.1)
- 5 tabs simultaneas (menos que cnpj.biz - Maps detecta bots mais facilmente)
- Queries em ordem de prioridade:
  1. `"{nome_fantasia} {cidade} {uf}"`
  2. `"{logradouro} {numero} {cidade} {uf}"`
  3. `"{nome_fantasia} {logradouro} {cidade}"`
- Detecta resultado unico (URL `/place/`) vs lista
- Em lista: tenta ate 3 primeiros resultados
- Score minimo: 0.50 (mais tolerante que cruzamento generico)
- Descarta resultados de outra cidade
- Auto-ajuste de delays em falhas consecutivas

### Address Matcher (Cruzamento)
- Normaliza enderecos: remove acentos, expande abreviacoes (R. -> Rua, Av. -> Avenida)
- Score = numero (40%) + logradouro (40%) + bairro (20%)
- Numero diferente = descarta imediatamente (score 0.05)
- Score minimo para match: 0.55

### Export Excel
- 7 abas: Leads Receita (TODOS) | Leads Detalhados | Premium | Com Contato | Com Socios | Maps | Resumo
- Premium = COM iFood (tem delivery, potencial parceiro)
- `buscar_leads_receita()` retorna TODOS os CNPJs (sem filtro)
- `buscar_leads_detalhados()` retorna so os com dados completos

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

## Stack Tecnica

- **Linguagem**: Python 3.12
- **Ambiente virtual**: `.venv/`
- **Banco de dados**: SQLite (`data/restaurants.db`)
- **Scraping**: Playwright (Chromium, modo async)
- **HTTP**: httpx (async)
- **Dados**: pandas, openpyxl
- **UI terminal**: rich
- **Sem framework web** - aplicacao CLI com menu interativo
